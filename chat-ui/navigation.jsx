import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Sidebar, Button } from '@apeiron/ui';
import { Hash, MessagesSquare, Search, Settings, UserRound } from 'lucide-react';

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

function ChatNavigation({ api, workspace }) {
    const rooms = useRooms(api.stores.roomListStore);
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
    function openNative(selector, revealDirectory = true) {
        const activate = () => {
            if (!workspace.isConnected) return;
            if (revealDirectory) showDirectory(true);
            requestAnimationFrame(() => {
                const control = workspace.querySelector(selector);
                if (!control) return;
                if (control.getAttribute('aria-haspopup') === 'menu') {
                    control.focus();
                    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                } else control.click();
            });
        };
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
    return <Sidebar aria-label={text('会话导航', 'Chat navigation')}
        collapsed={collapsed} onCollapsedChange={setCollapsed}
        logo={<img alt="Apeiron" className="ap-brand" src="/apeiron-chat/wordmark.svg" />}
        title={collapsed ? undefined : 'Chat'} items={items}
        selectedKeys={directory ? ['directory'] : selected ? [selected.id] : []}
        onClick={({ key }) => {
            if (key === 'directory') { showDirectory(!directory); return; }
            if (key === 'search') { openNative('#room-list-search-button', false); return; }
            showDirectory(false);
            api.navigation.openRoom(key);
        }}
        footer={<div className="flex items-center justify-between gap-1">
            <Button variant="ghost" icon={<UserRound />} iconOnly={collapsed}
                aria-label={text('账户', 'Account')} onClick={() => openNative('.mx_UserMenu button')}>
                {collapsed ? undefined : text('账户', 'Account')}
            </Button>
            {!collapsed && <Button variant="ghost" iconOnly icon={<Settings />}
                aria-label={text('快速设置', 'Quick settings')} onClick={() => openNative('.mx_QuickSettingsButton')} />}
        </div>}
    />;
}

export function mountNavigation(node, api, workspace) {
    const root = createRoot(node, { identifierPrefix: 'apeiron-chat-' });
    root.render(<ChatNavigation api={api} workspace={workspace} />);
    return () => root.unmount();
}
