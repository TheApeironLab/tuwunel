// Element owns the interactive credential form projected through the slot.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { LoginPage } from '@apeiron/ui';

export function mountLogin(node, english) {
    const root = createRoot(node, { identifierPrefix: 'apeiron-login-' });
    root.render(
        <LoginPage
            logo={<img src="/apeiron-chat/wordmark.svg" alt="Apeiron" width="180" height="48" />}
            heading="Apeiron Chat"
            subheading={english ? 'A shared workspace for team conversations.' : '团队会话与文件共享空间。'}
            features={english
                ? ['Talk with colleagues in rooms', 'Share files and attachments', 'Search past conversations']
                : ['在房间中与团队成员讨论', '发送文件和附件', '搜索历史会话']}
            title={english ? 'Welcome back' : '欢迎登录'}
            description={english ? 'Sign in with your team account.' : '使用团队账号登录。'}
            copyright="Apeiron Chat / Apeiron Lab"
        >
            <slot />
        </LoginPage>
    );
    return () => root.unmount();
}
