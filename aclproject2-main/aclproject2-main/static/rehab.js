'use strict';
window.Rehab = (() => {
    let statusTimer;
    function notify(message, kind = 'error', persistent = false) {
        let box = document.getElementById('rehab-status');
        if (!box) {
            box = document.createElement('div');
            box.id = 'rehab-status';
            box.className = 'rehab-status';
            box.setAttribute('role', 'status');
            box.setAttribute('aria-live', 'polite');
            document.body.appendChild(box);
        }
        clearTimeout(statusTimer);
        box.textContent = message;
        box.dataset.kind = kind;
        box.hidden = false;
        if (!persistent) statusTimer = setTimeout(() => { box.hidden = true; }, 7000);
    }
    async function api(url, options = {}) {
        const headers = new Headers(options.headers || {});
        const method = (options.method || 'GET').toUpperCase();
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
        }
        headers.set('Accept', 'application/json');
        const controller = new AbortController();
        const abort = () => controller.abort();
        const signal = options.signal;
        if (signal?.aborted) controller.abort();
        else signal?.addEventListener('abort', abort, {once: true});
        const timeout = setTimeout(abort, 10000);
        try {
            const response = await fetch(url, {...options, headers, signal: controller.signal, credentials: 'same-origin', cache: 'no-store'});
            const isJson = (response.headers.get('content-type') || '').includes('application/json');
            const data = isJson ? await response.json() : null;
            if (!response.ok || response.redirected || !data || data.success === false) {
                const fallback = response.status === 401 || response.redirected
                    ? 'Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.'
                    : 'Không thể hoàn tất yêu cầu. Vui lòng thử lại.';
                const error = new Error(data?.message || fallback);
                error.status = response.status;
                throw error;
            }
            return data;
        } catch (error) {
            if (error.name === 'AbortError' && !signal?.aborted) throw new Error('Máy chủ phản hồi chậm. Vui lòng thử lại.');
            if (error instanceof TypeError) throw new Error('Mất kết nối với máy chủ. Kiểm tra kết nối rồi thử lại.');
            throw error;
        } finally {
            clearTimeout(timeout);
            signal?.removeEventListener('abort', abort);
        }
    }
    // Schedule only after the previous request settles; pause while the tab is hidden.
    function poll(task, interval = 300, onError = error => notify(error.message)) {
        let timer, controller, stopped = false, busy = false, failures = 0;
        async function tick() {
            clearTimeout(timer);
            if (stopped || busy || document.hidden) return;
            busy = true;
            controller = new AbortController();
            try {
                await task(controller.signal);
                failures = 0;
            } catch (error) {
                if (error.name !== 'AbortError') {
                    failures++;
                    if (failures === 1 || failures % 10 === 0) onError(error);
                    if (error.status === 401 || error.status === 403) stopped = true;
                }
            } finally {
                busy = false;
                if (!stopped && !document.hidden) timer = setTimeout(tick, Math.min(10000, interval * Math.pow(2, Math.min(failures, 5))));
            }
        }
        function visibility() {
            clearTimeout(timer);
            if (document.hidden) controller?.abort();
            else tick();
        }
        function stop() {
            stopped = true;
            clearTimeout(timer);
            controller?.abort();
            document.removeEventListener('visibilitychange', visibility);
            window.removeEventListener('pagehide', stop);
        }
        document.addEventListener('visibilitychange', visibility);
        window.addEventListener('pagehide', stop);
        tick();
        return stop;
    }
    async function action(button, task) {
        if (button?.disabled) return;
        if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
        try { return await task(); }
        catch (error) { notify(error.message); }
        finally { if (button) { button.disabled = false; button.removeAttribute('aria-busy'); } }
    }
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('th').forEach(th => th.scope ||= 'col');
        document.querySelectorAll('table').forEach(table => {
            let wrapper = table.parentElement;
            if (!wrapper.classList.contains('table-scroll')) {
                wrapper = document.createElement('div');
                wrapper.className = 'responsive-table';
                table.before(wrapper);
                wrapper.appendChild(table);
            }
            wrapper.tabIndex = 0;
            wrapper.setAttribute('role', 'region');
            wrapper.setAttribute('aria-label', 'Bảng số liệu; cuộn ngang để xem thêm');
        });
        document.querySelectorAll('button[title]').forEach(button => {
            if (!button.hasAttribute('aria-label')) button.setAttribute('aria-label', button.title);
        });
        document.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => {
            const button = form.querySelector('button[type="submit"]');
            if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
        }));
    });
    window.addEventListener('offline', () => notify('Thiết bị đang ngoại tuyến. Dữ liệu trực tiếp tạm dừng.', 'error', true));
    window.addEventListener('online', () => notify('Đã có mạng. Đang kết nối lại máy chủ…', 'info'));
    window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
    return {api, poll, action, notify};
})();
