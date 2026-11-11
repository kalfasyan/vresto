// Keeps what the map shows in the address bar, so a link or bookmark reopens the same view,
// and remembers the last map view in this browser for the next visit without a link.
window.vrestoUrl = (function () {
    const VIEW_KEY = 'vresto.view';
    const params = function () { return new URLSearchParams(window.location.search); };
    return {
        get: function (key) { return params().get(key); },
        set: function (patch) {
            const p = params();
            Object.keys(patch).forEach(function (key) {
                const value = patch[key];
                if (value === null || value === undefined || value === '') p.delete(key);
                else p.set(key, value);
            });
            const query = p.toString();
            window.history.replaceState(window.history.state, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash);
            if ('ll' in patch || 'z' in patch) {
                try { localStorage.setItem(VIEW_KEY, JSON.stringify({ll: p.get('ll'), z: p.get('z')})); } catch (e) { /* storage blocked */ }
            }
        },
        lastView: function () {
            try {
                const view = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null');
                if (!view || !view.ll) return null;
                const parts = view.ll.split(',').map(Number);
                const zoom = Number(view.z);
                if (parts.length !== 2 || !parts.every(isFinite) || !isFinite(zoom)) return null;
                return {center: parts, zoom: zoom};
            } catch (e) {
                return null;
            }
        },
    };
})();
