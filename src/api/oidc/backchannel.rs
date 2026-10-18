use axum::{
	extract::{Form, Path, State},
	response::IntoResponse,
};
use http::StatusCode;
use serde::Deserialize;

use super::oauth_error;
use crate::ClientIp;

#[derive(Deserialize)]
pub(crate) struct Logout {
	logout_token: String,
}

pub(crate) async fn logout_route(
	State(services): State<crate::State>,
	ClientIp(client): ClientIp,
	Path(provider): Path<String>,
	Form(body): Form<Logout>,
) -> impl IntoResponse {
	let result: tuwunel_core::Result = async {
		services.oauth.check_device_rate_limit(client)?;
		let provider = services.oauth.providers.get(&provider).await?;
		let claims = services
			.oauth
			.verify_upstream_jwt(&provider, &body.logout_token, true)
			.await?;
		services
			.oauth
			.backchannel
			.revoke(&provider, &claims)
			.await
	}
	.await;
	let response = match result {
		| Ok(()) => StatusCode::OK.into_response(),
		| Err(error) if error.status_code() == StatusCode::TOO_MANY_REQUESTS => {
			StatusCode::TOO_MANY_REQUESTS.into_response()
		},
		| Err(error) if error.status_code().is_server_error() => oauth_error(
			StatusCode::SERVICE_UNAVAILABLE,
			"temporarily_unavailable",
			"Logout verification unavailable",
		),
		| Err(_) => {
			oauth_error(StatusCode::BAD_REQUEST, "invalid_request", "Logout token rejected")
		},
	};
	([(http::header::CACHE_CONTROL, "no-store")], response)
}
