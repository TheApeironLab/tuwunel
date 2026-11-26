// Element v1.12.30 exposes the login renderer, room list and navigation in Module API v2.
function asset(path) {
    const url = new URL(path, import.meta.url);
    url.search = new URL(import.meta.url).search;
    return url.href;
}

function stylesheet(path) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = asset(path);
    return link;
}

function registerLogin(mountLogin) {
    customElements.define('apeiron-chat-login', class extends HTMLElement {
        connectedCallback() {
            if (this.unmount) return;
            const shadow = this.shadowRoot ?? this.attachShadow({ mode: 'open' });
            const frame = document.createElement('main');
            frame.className = 'ap-scope';
            shadow.replaceChildren(stylesheet('components.css'), stylesheet('beams.css'), stylesheet('login.css'), frame);
            const english = !document.documentElement.lang.toLowerCase().startsWith('zh');
            this.unmount = mountLogin(frame, english);
            this.setAttribute('data-ready', '');
        }

        disconnectedCallback() {
            const unmount = this.unmount;
            queueMicrotask(() => {
                if (!this.isConnected && this.unmount === unmount) {
                    unmount?.();
                    this.unmount = null;
                }
            });
        }
    });
}

export default class ApeironTheme {
    static moduleApiVersion = '^2.2.0';
    constructor(api) { this.api = api; }

    async load() {
        document.head.append(stylesheet('theme.css'), stylesheet('components.css'));
        const { createElement, mountLogin, mountNavigation } = await import(asset('navigation.js'));
        registerLogin(mountLogin);
        this.api.customComponents.registerLoginComponent((props, original) =>
            createElement('apeiron-chat-login', null, original(props)));

        // Element mounts a new workspace on sign-in and removes it on sign-out.
        // Install one navigation tree per workspace and release its room subscriptions.
        let workspace, node, unmount;
        const sync = () => {
            if (workspace?.isConnected) return;
            if (workspace) { unmount(); node.remove(); }
            workspace = this.api.rootNode.querySelector('.mx_MatrixChat');
            if (!workspace) return;
            node = document.createElement('div');
            node.className = 'ap-nav ap-scope';
            workspace.prepend(node);
            unmount = mountNavigation(node, this.api, workspace);
            workspace.classList.add('ap-workspace');
            reflectTheme();
        };
        const reflectTheme = () => node?.classList.toggle('dark', document.body.classList.contains('cpd-theme-dark'));
        new MutationObserver(sync).observe(this.api.rootNode, { subtree: true, childList: true });
        new MutationObserver(reflectTheme).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        sync();
    }
}
