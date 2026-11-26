import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { Sidebar, Button, confirm } from '@apeiron/ui';
import { ArrowLeft, Hash, LogOut, MessagesSquare, Search, Settings, UserRound } from 'lucide-react';

export { createElement } from 'react';
export { mountLogin } from './login';

function useRooms(store) {
    const [rooms, setRooms] = useState([]);
    useEffect(() => {
        let active = true;
        let list;
        let visible = [];
        const refresh = () => setRooms(visible.map(room => ({ id: room.id, label: room.name.value })));
        function update(current) {
            for (const room of visible) room.name.unwatch(refresh);
            visible = current.slice(0, 12);
            for (const room of visible) room.name.watch(refresh);
            refresh();
        }
        store.waitForReady().then(() => {
            if (!active) return;
            list = store.getRooms();
            list.watch(update);
            update(list.value);
        }).catch(() => { /* The native room directory remains available. */ });
        return () => {
            active = false;
            list?.unwatch(update);
            for (const room of visible) room.name.unwatch(refresh);
        };
    }, [store]);
    return rooms;
}

function afterDrawerCloses(activate) {
    // Let the mobile drawer release its focus trap before opening a native menu.
    const drawer = document.querySelector('[role="dialog"]:has([data-sidebar-trigger])');
    if (!drawer) { activate(); return; }
    const observer = new MutationObserver(() => {
        if (drawer.isConnected) return;
        observer.disconnect();
        activate();
    });
    observer.observe(drawer.parentNode, { childList: true });
    drawer.querySelector('[data-sidebar-trigger]').click();
}

function ChatNavigation({ api, workspace }) {
    const rooms = useRooms(api.stores.roomListStore);
    const pendingSignOut = useRef(null);
    useEffect(() => () => pendingSignOut.current?.(), []);
    const [route, setRoute] = useState(location.hash);
    const [directory, setDirectory] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    const english = !document.documentElement.lang.toLowerCase().startsWith('zh');
    const text = (zh, en) => english ? en : zh;
    function showDirectory(open) {
        workspace.classList.toggle('ap-directory', open);
        setDirectory(open);
    }
    useEffect(() => {
        function navigate() { setRoute(location.hash); showDirectory(false); }
        function escape(e) {
            if (e.key === 'Escape' && workspace.classList.contains('ap-directory') && !document.querySelector('[role="dialog"]')) {
                showDirectory(false);
                workspace.querySelector('.ap-nav button')?.focus();
            }
        }
        function chooseRoom(event) {
            if (event.target instanceof Element && event.target.closest('.mx_RoomListItemView')) showDirectory(false);
        }
        workspace.addEventListener('click', chooseRoom);
        window.addEventListener('hashchange', navigate);
        window.addEventListener('keydown', escape);
        return () => {
            workspace.removeEventListener('click', chooseRoom);
            window.removeEventListener('hashchange', navigate);
            window.removeEventListener('keydown', escape);
        };
    }, [workspace]);
    const selected = rooms.find(room => route.split('/')[2] === room.id || route.split('/')[2] === encodeURIComponent(room.id));
    const items = [
        { key: 'directory', label: text('所有会话', 'All conversations'), icon: <MessagesSquare /> },
        { key: 'search', label: text('搜索', 'Search'), icon: <Search /> },
        ...(rooms.length ? [{ type: 'group', key: 'rooms', label: text('最近会话', 'Recent conversations'),
            children: rooms.map(room => ({ key: room.id, label: room.label, icon: <Hash /> })) }] : []),
    ];
    function openNative(selector, trigger) {
        // Read before the mobile drawer unmounts its button.
        const rect = trigger?.getBoundingClientRect();
        afterDrawerCloses(() => {
            const control = workspace.querySelector(selector);
            if (!control || !workspace.isConnected) return;
            if (rect) {
                workspace.style.setProperty('--ap-menu-x', rect.left + 'px');
                workspace.style.setProperty('--ap-menu-y', rect.top + 'px');
                workspace.style.setProperty('--ap-menu-w', rect.width + 'px');
                workspace.style.setProperty('--ap-menu-h', rect.height + 'px');
            }
            if (control.getAttribute('aria-haspopup') === 'menu') {
                control.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            } else control.click();
        });
    }
    function openSignOut() {
        afterDrawerCloses(() => {
            if (!workspace.isConnected || pendingSignOut.current) return;
            if (document.documentElement.classList.contains('ap-plaintext')) {
                const title = text('退出登录', 'Sign out');
                const dialog = confirm({
                    className: 'ap-signout',
                    title,
                    content: text('退出这台设备上的 Chat？', 'Sign out of Chat on this device?'),
                    cancelButtonProps: { children: text('取消', 'Cancel') },
                    okButtonProps: { children: title },
                });
                pendingSignOut.current = dialog.close;
                dialog.then(accepted => {
                    pendingSignOut.current = null;
                    if (accepted && workspace.isConnected) window.mxDispatcher.dispatch({ action: 'logout' });
                });
                return;
            }
            // Element's module API has no sign-out method. Use the account tab's
            // own action so Element keeps its device recovery and logout flow.
            const selector = '.mx_UserSettingsDialog .mx_UserProfileSettings_profile_buttons .mx_AccessibleButton_kind_danger_outline';
            const observer = new MutationObserver(() => {
                const button = document.querySelector(selector);
                if (!button || !workspace.isConnected) return;
                stop();
                const closeSettings = button.closest('.mx_UserSettingsDialog').querySelector('.mx_Dialog_cancelButton');
                button.click();
                closeSettings?.click();
            });
            const timeout = setTimeout(stop, 5000);
            function stop() {
                observer.disconnect();
                clearTimeout(timeout);
                pendingSignOut.current = null;
            }
            pendingSignOut.current = stop;
            observer.observe(document.body, { childList: true, subtree: true });
            window.mxDispatcher.dispatch({ action: 'view_user_settings', initialTabId: 'USER_ACCOUNT_TAB' });
        });
    }
    const signOutLabel = text('退出登录', 'Sign out');
    const directoryPanel = directory && workspace.querySelector('.mx_LeftPanel_outerWrapper');
    return <>
        {directoryPanel && createPortal(
            <div className="ap-directory-tools ap-scope">
                <Button variant="ghost" icon={<ArrowLeft />} onClick={() => {
                    showDirectory(false);
                    requestAnimationFrame(() => workspace.querySelector('.ap-nav button')?.focus());
                }}>{text('返回会话导航', 'Back to chat navigation')}</Button>
                <Button variant="ghost" iconOnly icon={<LogOut />} aria-label={signOutLabel}
                    title={signOutLabel} onClick={openSignOut} />
            </div>, directoryPanel)}
        <Sidebar aria-label={text('会话导航', 'Chat navigation')}
            collapsed={collapsed} onCollapsedChange={setCollapsed}
            logo={<img alt="Apeiron" className="ap-brand" src="/apeiron-chat/wordmark.svg" />}
            title={collapsed ? undefined : 'Chat'} items={items}
            selectedKeys={directory ? ['directory'] : selected ? [selected.id] : []}
            onClick={({ key }) => {
                if (key === 'directory') { afterDrawerCloses(() => showDirectory(true)); return; }
                if (key === 'search') { openNative('#room-list-search-button'); return; }
                showDirectory(false);
                api.navigation.openRoom(key);
            }}
            footer={<div className="flex flex-col gap-1">
                <div className={collapsed ? 'flex flex-col items-center gap-1' : 'flex items-center justify-between gap-1'}>
                    <Button variant="ghost" icon={<UserRound />} iconOnly={collapsed}
                        aria-label={text('账户', 'Account')} onClick={event => openNative('.mx_UserMenu button', event.currentTarget)}>
                        {collapsed ? undefined : text('账户', 'Account')}
                    </Button>
                    <Button variant="ghost" iconOnly icon={<Settings />}
                        aria-label={text('快速设置', 'Quick settings')} onClick={event => openNative('.mx_QuickSettingsButton', event.currentTarget)} />
                </div>
                <Button variant="ghost" icon={<LogOut />} iconOnly={collapsed}
                    aria-label={signOutLabel} onClick={openSignOut}>
                    {collapsed ? undefined : signOutLabel}
                </Button>
            </div>}
        />
    </>;
}

export function mountNavigation(node, api, workspace) {
    const root = createRoot(node, { identifierPrefix: 'apeiron-chat-' });
    root.render(<ChatNavigation api={api} workspace={workspace} />);
    return () => root.unmount();
}
