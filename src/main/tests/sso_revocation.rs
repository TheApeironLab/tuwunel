//! Session-level revocation must survive restart and must fence refresh races.
use std::{env, fs, path::Path, process::Command};

use serde_json::json;
use tuwunel::{Args, Runtime, Server, async_run, async_start, async_stop};
use tuwunel_core::{Result, ruma::UserId, utils::time::now};
use tuwunel_service::{Services, oauth::backchannel::Binding};

const ISSUER: &str = "https://iam.example.internal/realms/master";
const ACCESS_A: &str = "test_access_a_000000000000000000000";
const ACCESS_B: &str = "test_access_b_000000000000000000000";
const REFRESH_A: &str = "refresh_test_a_000000000000000000000";

#[test]
fn session_revocation_is_scoped_durable_and_fences_late_tokens() -> Result {
	if let Ok(phase) = env::var("SSO_REVOCATION_TEST_PHASE") {
		let database = env::var("SSO_REVOCATION_TEST_DATABASE").unwrap();
		return boot(Path::new(&database), &phase);
	}
	let database = Args::test_database_path("sso-revocation");
	let result = (|| -> Result {
		for phase in ["seed", "restart"] {
			let status = Command::new(env::current_exe()?)
				.env("SSO_REVOCATION_TEST_PHASE", phase)
				.env("SSO_REVOCATION_TEST_DATABASE", &database)
				.status()?;
			assert!(status.success(), "{phase} failed");
		}
		Ok(())
	})();
	fs::remove_dir_all(database).ok();
	result
}

fn boot(database: &Path, phase: &str) -> Result {
	let args = Args::default_test(if phase == "seed" { &["fresh"] } else { &[] })
		.with_database_path(database)
		.with_option("sso_require_session_binding=true")
		.with_option("identity_provider.test.brand=\"keycloak\"")
		.with_option("identity_provider.test.client_id=\"matrix-app\"")
		.with_option("identity_provider.test.client_secret=\"test-only-unused\"")
		.with_option(format!("identity_provider.test.issuer_url={ISSUER:?}"));
	let runtime = Runtime::new(Some(&args))?;
	let server = Server::new(Some(&args), Some(&runtime))?;
	runtime.block_on(async {
		let services = async_start(&server).await?;
		let outcome = exercise(&services, phase).await;
		let shutdown = server.server.shutdown();
		drop(services);
		let run = async_run(&server).await;
		let stop = async_stop(&server).await;
		outcome.and(shutdown).and(run).and(stop)
	})
}

async fn exercise(services: &Services, phase: &str) -> Result {
	let backchannel = &services.oauth.backchannel;
	if phase == "restart" {
		assert!(
			services
				.users
				.find_from_token(ACCESS_A)
				.await
				.is_err()
		);
		assert!(
			services
				.users
				.find_from_token(REFRESH_A)
				.await
				.is_err()
		);
		assert!(
			services
				.users
				.find_from_token(ACCESS_B)
				.await
				.is_ok()
		);
		return Ok(());
	}
	let user = UserId::parse_with_server_name("ssoalice", services.globals.server_name())?;
	services.users.create(&user, None, None).await?;
	let timestamp = now().as_secs();
	let a = Binding {
		provider: "matrix-app".into(),
		issuer: ISSUER.into(),
		subject: "alice".into(),
		sid: "browser-a".into(),
		authenticated_at: timestamp,
	};
	let b = Binding { sid: "browser-b".into(), ..a.clone() };
	backchannel
		.bind_tokens(Some(&a), ACCESS_A, Some(REFRESH_A))
		.await?;
	services
		.users
		.create_device(
			&user,
			Some("A".into()),
			(Some(ACCESS_A), None),
			Some(REFRESH_A),
			None,
			None,
		)
		.await?;
	backchannel
		.bind_tokens(Some(&b), ACCESS_B, None)
		.await?;
	services
		.users
		.create_device(&user, Some("B".into()), (Some(ACCESS_B), None), None, None, None)
		.await?;
	assert!(
		services
			.users
			.find_from_token(ACCESS_A)
			.await
			.is_ok()
	);
	assert!(
		services
			.users
			.find_from_token(ACCESS_B)
			.await
			.is_ok()
	);
	assert!(
		backchannel
			.token_binding("unbound-legacy-token")
			.await
			.is_err()
	);

	// Copy a binding before logout, as an in-flight refresh would do.
	let captured = backchannel.token_binding(REFRESH_A).await?;
	let provider = services
		.oauth
		.providers
		.get_config("matrix-app")?;
	let logout = json!({"iat":timestamp,"jti":"logout-1","sid":"browser-a","sub":"alice","events":{"http://schemas.openid.net/event/backchannel-logout":{}}});
	backchannel.revoke(&provider, &logout).await?;
	backchannel.revoke(&provider, &logout).await?; // Duplicate delivery is harmless.
	assert!(
		services
			.users
			.find_from_token(ACCESS_A)
			.await
			.is_err()
	);
	assert!(
		services
			.users
			.find_from_token(REFRESH_A)
			.await
			.is_err()
	);
	assert!(
		services
			.users
			.find_from_token(ACCESS_B)
			.await
			.is_ok()
	);
	assert!(
		backchannel
			.bind_tokens(captured.as_ref(), "late-access", Some("late-refresh"))
			.await
			.is_err()
	);
	assert!(
		backchannel
			.inherit(REFRESH_A, "renewed-access", Some("renewed-refresh"))
			.await
			.is_err()
	);
	assert!(matches!(
		services
			.users
			.classify_refresh_token(REFRESH_A)
			.await,
		tuwunel_service::users::device::RefreshToken::Unknown
	));

	// A new session is independent even when the account is unchanged.
	let new = Binding { sid: "new-browser-a".into(), ..a.clone() };
	backchannel
		.bind_tokens(Some(&new), "new-session", None)
		.await?;
	assert!(
		backchannel
			.token_binding("new-session")
			.await
			.is_ok()
	);
	Ok(())
}
