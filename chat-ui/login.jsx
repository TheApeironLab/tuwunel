import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { Button, LoginPage } from '@apeiron/ui';
import { Eye, EyeOff, Globe } from 'lucide-react';

// Element owns the live fields, authentication providers and submit handlers.
function ChatLoginPage({ host, english }) {
    const [password, setPassword] = useState(null);
    const [language, setLanguage] = useState(null);
    const [hasSso, setHasSso] = useState(false);
    const [revealed, setRevealed] = useState(false);
    const text = (zh, en) => english ? en : zh;

    useEffect(() => {
        function syncControls() {
            setPassword(host.querySelector('input[autocomplete="current-password"]'));
            setLanguage(host.querySelector('.mx_AuthBody_language .mx_Dropdown_input'));
            setHasSso(Boolean(host.querySelector('.mx_SSOButtons, .mx_Login_fullWidthButton')));
        }
        syncControls();
        const observer = new MutationObserver(syncControls);
        observer.observe(host, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, [host]);
    useEffect(() => {
        setRevealed(false);
        if (!password) return;
        password.type = 'password';
        const username = password.form?.querySelector('input[name=username]');
        if (username) username.placeholder = english ? 'Enter username' : '输入用户名';
        password.placeholder = english ? 'Enter password' : '输入密码';
    }, [password, english]);

    return <LoginPage
        logo={<img src="/apeiron-chat/wordmark.svg" alt="Apeiron" width="180" height="48" />}
        heading="Apeiron Chat"
        subheading={text('团队会话与文件共享空间。', 'A shared workspace for team conversations.')}
        features={[text('在房间中与团队成员讨论', 'Talk with colleagues in rooms'),
            text('发送文件和附件', 'Share files and attachments'),
            text('搜索历史会话', 'Search past conversations')]}
        title={text('欢迎使用 Apeiron Chat', 'Welcome to Apeiron Chat')}
        description={text('使用本站账号，进入团队会话。', 'Sign in with your site account to join your team.')}
        copyright="Apeiron Chat / Apeiron Lab"
    >
        <div className="grid gap-5">
            <slot />
            {language && createPortal(<Globe aria-hidden="true" className="ap-language-icon" />, language)}
            {password && createPortal(
                <button type="button" className="ap-password-toggle"
                    disabled={password.disabled} aria-pressed={revealed}
                    aria-label={text('显示密码', 'Show password')}
                    onClick={() => {
                        password.type = revealed ? 'password' : 'text';
                        setRevealed(!revealed);
                    }}>
                    {revealed ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
                </button>, password.parentElement)}
            {(password || hasSso) && <>
                <div className="grid gap-2">
                    {!hasSso && <Button type="button" size="xl" variant="secondary" disabled>
                        {text('SSO 统一登录', 'Single sign-on')}
                    </Button>}
                    <p className="text-center text-xs leading-5 text-ui-muted">{hasSso
                        ? text('使用组织的统一身份账号登录。', 'Sign in with your organization’s identity provider.')
                        : text('本站尚未配置 SSO。', 'Single sign-on is not configured on this site.')}</p>
                </div>
                <p className="text-center text-xs leading-5 text-ui-muted">
                    {text('账号由管理员创建；忘记密码请联系管理员。', 'Contact your administrator for account access or password resets.')}
                </p>
            </>}
        </div>
    </LoginPage>;
}

export function mountLogin(node, english) {
    const root = createRoot(node, { identifierPrefix: 'apeiron-login-' });
    root.render(<ChatLoginPage host={node.getRootNode().host} english={english} />);
    return () => root.unmount();
}
