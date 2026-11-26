// Element's file tile waits for a complete Blob before saving. Track the
// authenticated transfer in the card and keep Element's sandboxed save path.
function receiveFile(client, content, report) {
    const url = client.mxcUrlToHttp(content.url, undefined, undefined, undefined, false, false, true);
    const token = client.getAccessToken();
    if (!url || !token || new URL(url).origin !== new URL(client.getHomeserverUrl()).origin) {
        throw new Error('Invalid media endpoint or session');
    }
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', url);
        xhr.setRequestHeader('Authorization', 'Bearer ' + token);
        xhr.responseType = 'blob';
        xhr.onprogress = event => report(event.loaded, event.lengthComputable ? event.total : content.info?.size);
        xhr.onerror = xhr.onabort = () => reject(new Error('Transfer interrupted'));
        xhr.onload = () => {
            if (xhr.status < 200 || xhr.status >= 300) reject(new Error('HTTP ' + xhr.status));
            else resolve(xhr.response);
        };
        xhr.send();
    });
}

let saveFrame;
function saveFile(blob, name) {
    if (!saveFrame) {
        saveFrame = new Promise((resolve, reject) => {
            const iframe = document.createElement('iframe');
            iframe.hidden = true;
            iframe.setAttribute('sandbox', 'allow-scripts allow-downloads');
            iframe.onload = () => resolve(iframe);
            iframe.onerror = () => {
                iframe.remove();
                saveFrame = undefined;
                reject(new Error('Download frame unavailable'));
            };
            iframe.src = 'usercontent/';
            document.body.append(iframe);
        });
    }
    return saveFrame.then(frame => frame.contentWindow.postMessage({
        blob, download: name, auto: true, textContent: '', style: '', imgSrc: '',
    }, '*'));
}

function showProgress(status, received, total, zh) {
    const bar = status.querySelector('progress');
    const known = Number.isFinite(total) && total > 0 && total >= received;
    status.hidden = false;
    bar.hidden = false;
    if (known) {
        bar.max = total;
        bar.value = received;
    } else {
        bar.removeAttribute('value');
    }
    const mb = bytes => (bytes / 1048576).toFixed(1);
    const amount = known
        ? Math.floor(received * 100 / total) + '% · ' + mb(received) + ' / ' + mb(total)
        : mb(received);
    const text = status.querySelector('span');
    text.removeAttribute('role');
    text.textContent = (zh ? '正在下载 ' : 'Downloading ') + amount + ' MiB';
}

export function registerFileProgress(api, createElement) {
    const running = new Map();
    const cards = new WeakMap();
    api.customComponents.registerMessageRenderer(
        event => event.type === 'm.room.message' && event.content.msgtype === 'm.file' &&
            !event.content.file && event.content.url?.startsWith('mxc://'),
        ({ mxEvent }, original) => original && createElement('div', {
            'data-ap-file': mxEvent.eventId,
            ref: node => {
                if (!node) return;
                cards.set(node, mxEvent);
                const active = running.get(mxEvent.eventId);
                if (active) {
                    const status = node.querySelector(':scope > .ap-download-status');
                    active.views.add(status);
                    showProgress(status, active.received, active.total, api.i18n.language.toLowerCase().startsWith('zh'));
                }
            },
        }, original(), createElement('div', { className: 'ap-download-status', hidden: true },
            createElement('progress', { 'aria-label': api.i18n.translate('action|download') }),
            createElement('span'))));

    // The toolbar sits outside the message renderer. Both native download buttons
    // must join the same transfer when the user switches between them.
    api.rootNode.addEventListener('click', async event => {
        const button = event.target.closest?.('button,[role="button"]');
        if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true' ||
            button.getAttribute('aria-label') !== api.i18n.translate('action|download')) return;
        let card = button.closest('.mx_EventTile_content')?.closest('[data-ap-file]');
        if (!card && button.closest('[role="toolbar"]')) {
            const tile = button.closest('[data-event-id]');
            card = Array.from(tile?.querySelectorAll('[data-ap-file]') ?? [])
                .find(node => node.dataset.apFile === tile.dataset.eventId);
        }
        const message = cards.get(card);
        if (!message) return;
        event.preventDefault();
        event.stopPropagation();
        const status = card.querySelector(':scope > .ap-download-status');
        const zh = api.i18n.language.toLowerCase().startsWith('zh');
        const { content, eventId } = message;
        const active = running.get(eventId);
        if (active) {
            active.views.add(status);
            showProgress(status, active.received, active.total, zh);
            return;
        }
        const transfer = { views: new Set([status]), received: 0, total: content.info?.size };
        running.set(eventId, transfer);
        showProgress(status, 0, transfer.total, zh);
        let result;
        try {
            const blob = await receiveFile(window.mxMatrixClientPeg.safeGet(), content, (received, total) => {
                transfer.received = received;
                transfer.total = total;
                for (const view of transfer.views) {
                    if (view.isConnected) showProgress(view, received, total, zh);
                    else transfer.views.delete(view);
                }
            });
            await saveFile(blob, content.filename || content.body || 'download');
            result = zh ? '已交给浏览器保存' : 'Sent to browser for saving';
        } catch (error) {
            const code = /^HTTP \d{3}$/.test(error.message) ? ' (' + error.message + ')' : '';
            result = (zh ? '下载失败，请点击下载重试' : 'Download failed. Select download to retry.') + code;
        } finally {
            running.delete(eventId);
            for (const view of transfer.views) {
                view.querySelector('progress').hidden = true;
                const text = view.querySelector('span');
                text.setAttribute('role', 'status');
                text.textContent = result;
            }
        }
    }, true);
}
