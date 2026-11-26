// Render the public LoginPage at build time, as in the Vasi IAM theme.
// Element supplies the interactive form through the slot.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server.edge';
import { LoginPage } from '@apeiron/ui';

export function renderLogin(english) {
    return renderToStaticMarkup(
        <LoginPage
            logo={<img src="/apeiron-chat/wordmark.svg" alt="Apeiron" width="180" height="40" />}
            heading="Apeiron Chat"
            subheading={english ? 'Team conversations and file sharing' : '团队会话与文件共享'}
            title={english ? 'Welcome back' : '欢迎登录'}
            description={english ? 'Sign in to your team workspace.' : '登录账号，进入团队会话。'}
        >
            <slot />
        </LoginPage>
    );
}
