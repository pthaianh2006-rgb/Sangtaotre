'use strict';
(() => {
    const panel = document.getElementById('doctorAi');
    if (!panel) return;
    const button = document.getElementById('aiGenerate');
    const status = document.getElementById('aiStatus');
    const output = document.getElementById('aiOutput');
    const pid = panel.dataset.patientId;
    let busy = false, stopPoll = null;
    function finish() {
        busy = false;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        if (stopPoll) { stopPoll(); stopPoll = null; }
    }
    button.addEventListener('click', async () => {
        if (busy) return;
        busy = true; button.disabled = true; button.setAttribute('aria-busy', 'true');
        status.textContent = 'Đang khởi động AI cục bộ và tóm tắt dữ liệu…';
        output.textContent = '';
        try {
            const job = await Rehab.api(`/admin/patient/${pid}/ai-summary`, {method:'POST'});
            stopPoll = Rehab.poll(async signal => {
                const result = await Rehab.api(`/admin/patient/${pid}/ai-summary/${job.job_id}`, {signal});
                if (signal.aborted) return;
                if (result.state === 'running') return;
                finish();
                if (result.state === 'done') {
                    output.textContent = result.text;
                    status.textContent = 'Bản nháp AI đã tạo' + (result.elapsed_seconds ? ` (${result.elapsed_seconds} giây)` : '') + '. Kiểm tra số liệu trước khi sử dụng.';
                } else status.textContent = result.message || 'Chưa tạo được bản tóm tắt.';
            }, 1000, error => { finish(); status.textContent = error.message; });
        } catch (error) { finish(); status.textContent = error.message; }
    });
})();
