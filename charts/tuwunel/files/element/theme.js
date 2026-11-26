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

// The Chart's homeserver rejects room encryption. Element 1.12.30 otherwise
// initializes device crypto even when discovery says rooms must stay plaintext.
function applyRoomPolicy(config) {
    const server = config.get('default_server_config');
    const policy = server?.['io.element.e2ee'];
    if (policy?.force_disable !== true) return;
    const peg = window.mxMatrixClientPeg;
    if (typeof peg?.initClientCrypto !== 'function') {
        throw new Error('Unsupported Element client initialization for plaintext Chat');
    }
    // Preserve session startup and the normal sync/media settings independently
    // of device crypto. In particular, do not enable Element's low-bandwidth mode.
    peg.initClientCrypto = async function () {
        const client = this.safeGet();
        const discover = client.getClientWellKnown.bind(client);
        client.getClientWellKnown = () => ({
            ...(discover() ?? server),
            'io.element.e2ee': policy,
        });
    };
    document.documentElement.classList.add('ap-plaintext');
}

export default class ApeironTheme {
    static moduleApiVersion = '^2.2.0';
    constructor(api) { this.api = api; }

    async load() {
        applyRoomPolicy(this.api.config);
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
