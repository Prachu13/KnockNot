// Knock Not Dashboard
// Two modes: Live View & Setup (drag-to-draw rooms, click-to-place APs)

const API = '/api/v1';
let currentView = 'live';
let currentFloorId = null;
let floorData = null;
let heatmapData = null;
let mapImage = null;

// Canvas
const canvas = document.getElementById('map-canvas');
const ctx = canvas.getContext('2d');

// Drawing state
let drawMode = null;        // null | 'room' | 'ap'
let dragStart = null;       // {x, y} pixel coords of mousedown
let dragEnd = null;         // {x, y} pixel coords during drag
let isDragging = false;
let pollInterval = null;
let animFrame = null;

// ─── Init ───
document.addEventListener('DOMContentLoaded', () => {
    loadFloors();
    checkEngineHealth();
    setInterval(checkEngineHealth, 10000);
    switchView('live');  // Set initial view state (shows legend, hides sidebar)
});

// ─── View Switching ───
function switchView(view) {
    currentView = view;
    document.querySelectorAll('.tab').forEach(t =>
        t.classList.toggle('active', t.dataset.view === view)
    );

    const sidebar = document.getElementById('sidebar');
    const legend = document.getElementById('legend');
    const addBtn = document.getElementById('btn-add-floor');
    const delBtn = document.getElementById('btn-delete-floor');

    if (view === 'setup') {
        sidebar.classList.remove('hidden');
        legend.classList.add('hidden');
        addBtn.style.display = '';
        delBtn.style.display = '';
        stopPolling();
        if (floorData) renderSidebar();
    } else {
        sidebar.classList.add('hidden');
        legend.classList.remove('hidden');
        addBtn.style.display = 'none';
        delBtn.style.display = 'none';
        if (currentFloorId) startPolling();
    }
    cancelDraw();
    render();
}

// ─── Floor Management ───
async function loadFloors() {
    try {
        const resp = await fetch(`${API}/floors`);
        const data = await resp.json();
        const select = document.getElementById('floor-select');
        select.innerHTML = '<option value="">Select a floor...</option>';
        for (const f of data.floors) {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = `${f.name}${f.location ? ' — ' + f.location : ''}`;
            select.appendChild(opt);
        }
        if (currentFloorId) select.value = currentFloorId;
    } catch (e) {
        console.error('Failed to load floors:', e);
    }
}

async function onFloorChange() {
    const val = document.getElementById('floor-select').value;
    currentFloorId = val ? parseInt(val) : null;

    // Clear stale data from previous floor immediately
    heatmapData = null;
    floorData = null;
    mapImage = null;
    renderSidebar();
    render();

    if (currentFloorId) {
        await loadFloorData(currentFloorId);
    } else {
        document.getElementById('no-floor-msg').classList.remove('hidden');
    }
    cancelDraw();
    render();
    if (currentView === 'live' && currentFloorId) startPolling();
    else stopPolling();
}

async function loadFloorData(floorId) {
    try {
        const resp = await fetch(`${API}/floors/${floorId}`);
        floorData = await resp.json();
        document.getElementById('no-floor-msg').classList.add('hidden');
        await loadMapImage(floorData.image_path);
        if (currentView === 'setup') renderSidebar();
    } catch (e) {
        console.error('Failed to load floor:', e);
    }
}

function loadMapImage(path) {
    return new Promise(resolve => {
        const img = new Image();
        img.onload = () => { mapImage = img; fitCanvas(); resolve(); };
        img.onerror = () => { mapImage = null; fitCanvas(); resolve(); };
        img.src = '/' + path;
    });
}

function showAddFloorModal() {
    openModal('Add Floor', 'Upload a floor plan image and set its real-world dimensions.', `
        <label>Floor Name</label>
        <input id="inp-floor-name" placeholder="e.g., 3rd Floor - Building A">
        <label>Location</label>
        <input id="inp-floor-location" placeholder="e.g., Santa Clara HQ">
        <label>Width (meters)</label>
        <input id="inp-floor-width" type="number" step="0.1" placeholder="50">
        <label>Height (meters)</label>
        <input id="inp-floor-height" type="number" step="0.1" placeholder="30">
        <label>Floor Map Image</label>
        <input id="inp-floor-image" type="file" accept="image/*">
        <div class="modal-actions">
            <button class="btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn-primary" onclick="submitAddFloor()">Create Floor</button>
        </div>
    `);
}

async function submitAddFloor() {
    const name = document.getElementById('inp-floor-name').value.trim();
    const location = document.getElementById('inp-floor-location').value.trim();
    const width = parseFloat(document.getElementById('inp-floor-width').value);
    const height = parseFloat(document.getElementById('inp-floor-height').value);
    const fileInput = document.getElementById('inp-floor-image');

    if (!name || !width || !height || !fileInput.files.length) {
        alert('Please fill all required fields and select an image.');
        return;
    }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('location', location);
    fd.append('width_meters', width);
    fd.append('height_meters', height);
    fd.append('image', fileInput.files[0]);

    try {
        const resp = await fetch(`${API}/floors`, { method: 'POST', body: fd });
        const floor = await resp.json();
        closeModal();
        await loadFloors();
        document.getElementById('floor-select').value = floor.id;
        currentFloorId = floor.id;
        await loadFloorData(floor.id);
        render();
    } catch (e) { alert('Failed to create floor.'); }
}

async function deleteCurrentFloor() {
    if (!currentFloorId || !confirm('Delete this floor and all its rooms & APs?')) return;
    await fetch(`${API}/floors/${currentFloorId}`, { method: 'DELETE' });
    currentFloorId = null;
    floorData = null;
    mapImage = null;
    await loadFloors();
    document.getElementById('no-floor-msg').classList.remove('hidden');
    render();
}

// ─── Modal helpers ───
function openModal(title, subtitle, bodyHTML) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-subtitle').textContent = subtitle || '';
    document.getElementById('modal-body').innerHTML = bodyHTML;
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

// ─── Sidebar ───
function renderSidebar() {
    const roomList = document.getElementById('room-list');
    const apList = document.getElementById('ap-list');
    roomList.innerHTML = '';
    apList.innerHTML = '';
    if (!floorData) return;

    for (const room of (floorData.rooms || [])) {
        const div = document.createElement('div');
        div.className = 'sidebar-item';
        div.innerHTML = `
            <div>
                <div class="item-name">${esc(room.name)}</div>
                <div class="item-meta">${room.calendar_id ? 'Calendar linked' : 'No calendar'}</div>
            </div>
            <div class="item-actions">
                <button class="btn-icon" onclick="renameRoom(${room.id}, '${esc(room.name)}')" title="Rename">&#9998;</button>
                <button class="btn-icon danger" onclick="deleteRoom(${room.id})" title="Delete">&times;</button>
            </div>
        `;
        roomList.appendChild(div);
    }

    for (const ap of (floorData.aps || [])) {
        const div = document.createElement('div');
        div.className = 'sidebar-item';
        div.innerHTML = `
            <div>
                <div class="item-name">${esc(ap.name)}</div>
                <div class="item-meta">${ap.ip_address}</div>
            </div>
            <div class="item-actions">
                <button class="btn-icon danger" onclick="deleteAP(${ap.id})" title="Delete">&times;</button>
            </div>
        `;
        apList.appendChild(div);
    }
}

function esc(s) { return s.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

// ─── Room Drawing (drag rectangle) ───
function startDrawRoom() {
    if (!floorData) return;
    drawMode = 'room';
    dragStart = null;
    dragEnd = null;
    isDragging = false;
    document.getElementById('canvas-container').classList.add('drawing-room');
    showDrawHint('Click and drag on the map to draw a room rectangle. Press <span class="key">Esc</span> to cancel.');
}

function finishRoomDraw() {
    if (!dragStart || !dragEnd) return;

    // Compute rectangle corners in meters
    const [mx1, my1] = pxToM(Math.min(dragStart.x, dragEnd.x), Math.min(dragStart.y, dragEnd.y));
    const [mx2, my2] = pxToM(Math.max(dragStart.x, dragEnd.x), Math.max(dragStart.y, dragEnd.y));

    // Too small?
    if (Math.abs(mx2 - mx1) < 0.5 || Math.abs(my2 - my1) < 0.5) {
        cancelDraw();
        return;
    }

    const polygon = [[mx1, my1], [mx2, my1], [mx2, my2], [mx1, my2]];

    openModal('Name This Room', 'This name should match the Google Calendar room resource name.', `
        <label>Room Name</label>
        <input id="inp-room-name" placeholder="e.g., Everest" autofocus>
        <label>Google Calendar Resource ID</label>
        <input id="inp-room-calendar" placeholder="e.g., arista.com_xxx@resource.calendar.google.com">
        <label>Color</label>
        <input id="inp-room-color" type="color" value="#c97b4b">
        <div class="modal-actions">
            <button class="btn-secondary" onclick="cancelDraw(); closeModal()">Cancel</button>
            <button class="btn-primary" onclick="submitRoom()">Save Room</button>
        </div>
    `);

    // Stash polygon for submitRoom
    window._pendingPolygon = polygon;
}

async function submitRoom() {
    const name = document.getElementById('inp-room-name').value.trim();
    const calendarId = document.getElementById('inp-room-calendar').value.trim();
    const color = document.getElementById('inp-room-color').value;
    const polygon = window._pendingPolygon;

    if (!name) { alert('Room name is required.'); return; }
    if (!polygon) { alert('No room shape drawn.'); return; }

    try {
        await fetch(`${API}/floors/${currentFloorId}/rooms`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, calendar_id: calendarId, polygon, color }),
        });
        closeModal();
        cancelDraw();
        await loadFloorData(currentFloorId);
        renderSidebar();
        render();
    } catch (e) { alert('Failed to create room.'); }

    delete window._pendingPolygon;
}

function renameRoom(roomId, currentName) {
    openModal('Rename Room', 'This name should match the Google Calendar room resource name.', `
        <label>New Room Name</label>
        <input id="inp-rename" value="${esc(currentName)}" autofocus>
        <div class="modal-actions">
            <button class="btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn-primary" onclick="submitRename(${roomId})">Save</button>
        </div>
    `);
    // Focus + select all after modal renders
    setTimeout(() => {
        const inp = document.getElementById('inp-rename');
        if (inp) { inp.focus(); inp.select(); }
    }, 50);
}

async function submitRename(roomId) {
    const name = document.getElementById('inp-rename').value.trim();
    if (!name) { alert('Name is required.'); return; }

    try {
        await fetch(`${API}/rooms/${roomId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        closeModal();
        await loadFloorData(currentFloorId);
        renderSidebar();
        render();
    } catch (e) { alert('Failed to rename room.'); }
}

async function deleteRoom(roomId) {
    if (!confirm('Delete this room?')) return;
    await fetch(`${API}/rooms/${roomId}`, { method: 'DELETE' });
    await loadFloorData(currentFloorId);
    renderSidebar();
    render();
}

// ─── AP Placement ───
function startPlaceAP() {
    if (!floorData) return;
    drawMode = 'ap';
    document.getElementById('canvas-container').classList.add('placing-ap');
    showDrawHint('Click on the map to place an Access Point. Press <span class="key">Esc</span> to cancel.');
}

function placeAP(px, py) {
    const [mx, my] = pxToM(px, py);
    openModal('Add Access Point', `Position: ${mx.toFixed(1)}m, ${my.toFixed(1)}m`, `
        <label>AP Name</label>
        <input id="inp-ap-name" placeholder="e.g., AP-3F-EVEREST-01" autofocus>
        <label>IP Address</label>
        <input id="inp-ap-ip" placeholder="e.g., 10.10.1.50">
        <label>MAC Address (optional)</label>
        <input id="inp-ap-mac" placeholder="e.g., aa:bb:cc:dd:ee:ff">
        <div class="modal-actions">
            <button class="btn-secondary" onclick="cancelDraw(); closeModal()">Cancel</button>
            <button class="btn-primary" onclick="submitAP(${mx}, ${my})">Save AP</button>
        </div>
    `);
}

async function submitAP(mx, my) {
    const name = document.getElementById('inp-ap-name').value.trim();
    const ip = document.getElementById('inp-ap-ip').value.trim();
    const mac = document.getElementById('inp-ap-mac').value.trim();

    if (!name || !ip) { alert('Name and IP are required.'); return; }

    try {
        await fetch(`${API}/floors/${currentFloorId}/aps`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, ip_address: ip, mac, x_meters: mx, y_meters: my }),
        });
        closeModal();
        cancelDraw();
        await loadFloorData(currentFloorId);
        renderSidebar();
        render();
    } catch (e) { alert('Failed to add AP.'); }
}

async function deleteAP(apId) {
    if (!confirm('Delete this AP?')) return;
    await fetch(`${API}/aps/${apId}`, { method: 'DELETE' });
    await loadFloorData(currentFloorId);
    renderSidebar();
    render();
}

function cancelDraw() {
    drawMode = null;
    dragStart = null;
    dragEnd = null;
    isDragging = false;
    delete window._pendingPolygon;
    const c = document.getElementById('canvas-container');
    c.classList.remove('drawing-room', 'placing-ap');
    hideDrawHint();
    render();
}

function showDrawHint(html) {
    const el = document.getElementById('draw-hint');
    el.innerHTML = html;
    el.classList.remove('hidden');
}

function hideDrawHint() {
    document.getElementById('draw-hint').classList.add('hidden');
}

// ─── Canvas Events ───
canvas.addEventListener('mousedown', (e) => {
    if (!floorData || currentView !== 'setup') return;
    const r = canvas.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;

    if (drawMode === 'room') {
        dragStart = { x: px, y: py };
        dragEnd = { x: px, y: py };
        isDragging = true;
    }
});

canvas.addEventListener('mousemove', (e) => {
    const r = canvas.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;

    // Live cursor coordinates (in meters)
    const coordsEl = document.getElementById('cursor-coords');
    if (coordsEl && floorData) {
        const [mx, my] = pxToM(px, py);
        coordsEl.textContent = `x: ${mx.toFixed(2)}m  y: ${my.toFixed(2)}m`;
        coordsEl.classList.add('visible');
    }

    // Room drag preview
    if (isDragging && drawMode === 'room') {
        dragEnd = { x: px, y: py };
        render();
        return;
    }

    // Tooltip in live mode
    if (currentView === 'live' && heatmapData) {
        const [mx, my] = pxToM(px, py);
        let hovered = null;
        for (const room of (heatmapData.rooms || [])) {
            if (room.polygon.length >= 3 && pip(mx, my, room.polygon)) {
                hovered = room;
                break;
            }
        }
        const tt = document.getElementById('tooltip');
        if (hovered) {
            const labels = {
                VACANT: 'Vacant', OCCUPIED: 'Occupied',
                MAYBE_GHOST: 'Ghost', GHOST_BOOKING: 'Ghost',
                ADHOC_USE: 'Ad-hoc', ADHOC_CONFIRMED: 'Ad-hoc'
            };
            let h = `<div class="tt-title">${hovered.name}</div>`;
            h += `<div class="tt-row">Status: <span>${labels[hovered.state] || hovered.state}</span></div>`;
            h += `<div class="tt-row">Devices: <span>${hovered.device_count}</span></div>`;
            if (hovered.current_event) {
                h += `<div class="tt-row">Meeting: <span>${hovered.current_event.title}</span></div>`;
                h += `<div class="tt-row">By: <span>${hovered.current_event.organizer}</span></div>`;
            }
            if (hovered.minutes_in_state > 0)
                h += `<div class="tt-row">For: <span>${Math.round(hovered.minutes_in_state)} min</span></div>`;
            tt.innerHTML = h;
            tt.style.left = (e.clientX + 14) + 'px';
            tt.style.top = (e.clientY + 14) + 'px';
            tt.classList.remove('hidden');
        } else {
            tt.classList.add('hidden');
        }
    }
});

canvas.addEventListener('mouseup', (e) => {
    if (isDragging && drawMode === 'room') {
        isDragging = false;
        const r = canvas.getBoundingClientRect();
        dragEnd = { x: e.clientX - r.left, y: e.clientY - r.top };

        // Check if rectangle is big enough
        const dx = Math.abs(dragEnd.x - dragStart.x);
        const dy = Math.abs(dragEnd.y - dragStart.y);
        if (dx > 10 && dy > 10) {
            finishRoomDraw();
        } else {
            // Too small, reset
            dragStart = null;
            dragEnd = null;
            render();
        }
    }
});

canvas.addEventListener('click', (e) => {
    if (!floorData || currentView !== 'setup') return;

    if (drawMode === 'ap') {
        const r = canvas.getBoundingClientRect();
        placeAP(e.clientX - r.left, e.clientY - r.top);
        drawMode = null;
        document.getElementById('canvas-container').classList.remove('placing-ap');
        hideDrawHint();
    }
});

canvas.addEventListener('mouseleave', () => {
    document.getElementById('tooltip').classList.add('hidden');
    const coordsEl = document.getElementById('cursor-coords');
    if (coordsEl) coordsEl.classList.remove('visible');
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && drawMode) cancelDraw();
});

// ─── Coordinates ───
function mToPx(mx, my) {
    if (!floorData) return [0, 0];
    return [(mx / floorData.width_meters) * canvas.width,
            (my / floorData.height_meters) * canvas.height];
}

function pxToM(px, py) {
    if (!floorData) return [0, 0];
    return [(px / canvas.width) * floorData.width_meters,
            (py / canvas.height) * floorData.height_meters];
}

function pip(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const [xi, yi] = poly[i], [xj, yj] = poly[j];
        if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi))
            inside = !inside;
    }
    return inside;
}

// ─── Rendering ───
function fitCanvas() {
    const container = document.getElementById('canvas-container');
    let w = container.clientWidth;
    let h = container.clientHeight;

    if (mapImage && floorData) {
        const imgR = mapImage.width / mapImage.height;
        const conR = w / h;
        if (imgR > conR) { h = w / imgR; }
        else { w = h * imgR; }
    }
    canvas.width = w;
    canvas.height = h;
    render();
}

window.addEventListener('resize', fitCanvas);

function render() {
    if (animFrame) cancelAnimationFrame(animFrame);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!floorData) return;

    // Background
    if (mapImage) {
        ctx.drawImage(mapImage, 0, 0, canvas.width, canvas.height);
        // Lighten overlay
        ctx.fillStyle = 'rgba(250,248,245,0.15)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    } else {
        ctx.fillStyle = '#faf8f5';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        // Subtle grid
        ctx.strokeStyle = '#ede7df';
        ctx.lineWidth = 0.5;
        const step = canvas.width / floorData.width_meters;
        for (let x = 0; x < canvas.width; x += step) {
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
        }
        for (let y = 0; y < canvas.height; y += step) {
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
        }
    }

    if (currentView === 'live') renderLive();
    else renderSetup();
}

function renderSetup() {
    // Existing rooms
    for (const room of (floorData.rooms || [])) {
        drawRoom(room.polygon, room.color || '#c97b4b', 0.2, room.name);
    }

    // Existing APs
    for (const ap of (floorData.aps || [])) {
        drawAPIcon(ap.x_meters, ap.y_meters, ap.name, true);
    }

    // Drag preview rectangle
    if (isDragging && dragStart && dragEnd) {
        const x = Math.min(dragStart.x, dragEnd.x);
        const y = Math.min(dragStart.y, dragEnd.y);
        const w = Math.abs(dragEnd.x - dragStart.x);
        const h = Math.abs(dragEnd.y - dragStart.y);

        ctx.fillStyle = 'rgba(201,123,75,0.12)';
        ctx.fillRect(x, y, w, h);

        ctx.strokeStyle = '#c97b4b';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);

        // Size label
        const [mx1, my1] = pxToM(x, y);
        const [mx2, my2] = pxToM(x + w, y + h);
        const wm = Math.abs(mx2 - mx1).toFixed(1);
        const hm = Math.abs(my2 - my1).toFixed(1);
        ctx.fillStyle = '#c97b4b';
        ctx.font = '12px DM Sans, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`${wm}m x ${hm}m`, x + w / 2, y + h / 2 + 5);
        ctx.textAlign = 'start';
    }
}

function renderLive() {
    // Map internal states to 4 user-facing states
    // MAYBE_GHOST → Ghost (it's already suspected, timer is internal detail)
    // ADHOC_CONFIRMED → Ad-hoc (confirmation is internal detail)
    const stateColor = {
        VACANT: '#5a9e6f',
        OCCUPIED: '#c75c5c',
        MAYBE_GHOST: '#d4943a',
        GHOST_BOOKING: '#d4943a',
        ADHOC_USE: '#5b8db8',
        ADHOC_CONFIRMED: '#5b8db8',
    };

    const liveRooms = heatmapData ? (heatmapData.rooms || []) : [];
    const liveAPs = heatmapData ? (heatmapData.aps || []) : [];
    const hasLiveData = liveRooms.length > 0;

    // Fallback: draw rooms from floorData before first poll
    if (!hasLiveData && floorData) {
        for (const room of (floorData.rooms || [])) {
            drawRoom(room.polygon, stateColor.VACANT, 0.2, room.name, 0);
        }
        for (const ap of (floorData.aps || [])) {
            drawAPIcon(ap.x_meters, ap.y_meters, ap.name, false);
        }
    }

    // Draw live rooms
    for (const room of liveRooms) {
        const c = stateColor[room.state] || stateColor.VACANT;
        const isGhost = room.state === 'GHOST_BOOKING' || room.state === 'MAYBE_GHOST';
        drawRoom(room.polygon, c, isGhost ? 0.35 : 0.2, room.name, room.device_count);
    }

    // Devices in rooms (orange dots)
    const t = Date.now() / 1000;
    for (const room of liveRooms) {
        for (const d of (room.devices || [])) {
            drawDeviceDot(d.x, d.y, d.mac_hash, t, '#c97b4b', 'rgba(201,123,75,0.1)');
        }
    }

    // Hallway / unassigned devices (grey dots — visible but outside any room)
    const hallwayDevices = (heatmapData && heatmapData.hallway_devices) || [];
    for (const d of hallwayDevices) {
        // Color intensity reflects confidence (more APs = brighter)
        const isMulti = d.ap_count >= 2;
        const dotColor = isMulti ? '#9b8e7e' : 'rgba(155,142,126,0.5)';
        const haloColor = isMulti ? 'rgba(155,142,126,0.15)' : 'rgba(155,142,126,0.08)';
        drawDeviceDot(d.x, d.y, d.mac_hash, t, dotColor, haloColor);
    }

    // APs
    for (const ap of liveAPs) {
        drawAPIcon(ap.x, ap.y, ap.name, ap.online);
    }

    // Stats bar — 4 simplified categories
    const statRooms = hasLiveData ? liveRooms : (floorData ? (floorData.rooms || []) : []);
    const s = document.getElementById('summary-stats');
    const isGhost = r => r.state === 'GHOST_BOOKING' || r.state === 'MAYBE_GHOST';
    const isOccupied = r => r.state === 'OCCUPIED';
    const isAdhoc = r => r.state === 'ADHOC_USE' || r.state === 'ADHOC_CONFIRMED';
    const ghosts = hasLiveData ? liveRooms.filter(isGhost).length : 0;
    const occupied = hasLiveData ? liveRooms.filter(isOccupied).length : 0;
    const adhocs = hasLiveData ? liveRooms.filter(isAdhoc).length : 0;
    const vacant = statRooms.length - occupied - ghosts - adhocs;
    const devices = heatmapData ? (heatmapData.total_devices || 0) : 0;

    s.innerHTML = `
        <div class="stat-item"><span class="stat-count">${statRooms.length}</span><span class="stat-label">Rooms</span></div>
        <div class="stat-item"><span class="stat-count" style="color:var(--green)">${vacant}</span><span class="stat-label">Vacant</span></div>
        <div class="stat-item"><span class="stat-count" style="color:var(--red)">${occupied}</span><span class="stat-label">Occupied</span></div>
        <div class="stat-item"><span class="stat-count" style="color:var(--orange)">${ghosts}</span><span class="stat-label">Ghost</span></div>
        <div class="stat-item"><span class="stat-count" style="color:var(--blue)">${adhocs}</span><span class="stat-label">Ad-hoc</span></div>
        <div class="stat-item"><span class="stat-count">${devices}</span><span class="stat-label">Devices</span></div>
    `;

    // Continue animating for device pulse
    if (currentView === 'live') animFrame = requestAnimationFrame(() => render());
}

function drawRoom(polygon, color, alpha, label, count) {
    if (!polygon || polygon.length < 3) return;
    const pts = polygon.map(([mx, my]) => mToPx(mx, my));

    // Fill
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fillStyle = hexA(color, alpha);
    ctx.fill();

    // Border
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Label
    if (label) {
        const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
        const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
        ctx.fillStyle = '#3d3529';
        ctx.font = '600 12px DM Sans, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, cx, cy - (count !== undefined ? 7 : 0));
        if (count !== undefined) {
            ctx.font = '11px DM Sans, sans-serif';
            ctx.fillStyle = '#9b8e7e';
            ctx.fillText(`${count} device${count !== 1 ? 's' : ''}`, cx, cy + 9);
        }
        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
    }
}

function drawDeviceDot(mx, my, macHash, t, color, haloColor) {
    const [px, py] = mToPx(mx, my);
    const pulse = 1 + 0.15 * Math.sin(t * 2.5 + macHash);
    const r = 4 * pulse;

    ctx.beginPath();
    ctx.arc(px, py, r + 5, 0, Math.PI * 2);
    ctx.fillStyle = haloColor;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(px, py, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
}


function drawAPIcon(mx, my, name, online) {
    const [px, py] = mToPx(mx, my);
    const color = online ? '#5b8db8' : '#b0a494';

    // Outer circle background
    ctx.beginPath();
    ctx.arc(px, py, 16, 0, Math.PI * 2);
    ctx.fillStyle = online ? 'rgba(91,141,184,0.12)' : 'rgba(176,164,148,0.1)';
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Router body
    const bw = 14, bh = 5;
    ctx.beginPath();
    ctx.rect(px - bw / 2, py + 1, bw, bh);
    ctx.fillStyle = color;
    ctx.fill();

    // Antenna stem
    ctx.beginPath();
    ctx.moveTo(px, py + 1);
    ctx.lineTo(px, py - 6);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.stroke();

    // Antenna tip dot
    ctx.beginPath();
    ctx.arc(px, py - 7, 2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // Signal arcs (only when online)
    if (online) {
        ctx.lineCap = 'round';
        for (let i = 1; i <= 2; i++) {
            ctx.beginPath();
            ctx.arc(px, py - 7, i * 5, -Math.PI * 0.8, -Math.PI * 0.2);
            ctx.strokeStyle = 'rgba(91,141,184,' + (0.6 - i * 0.15) + ')';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        }
    }
    ctx.lineCap = 'butt';

    // Name label
    if (name) {
        ctx.fillStyle = online ? '#5b8db8' : '#9b8e7e';
        ctx.font = '500 10px DM Sans, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(name, px, py + 26);
        ctx.textAlign = 'start';
    }
}

function hexA(hex, a) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
}

// ─── Polling ───
function startPolling() {
    stopPolling();
    heatmapData = null;  // Clear stale data from previous floor
    pollHeatmap();
    pollInterval = setInterval(pollHeatmap, 5000);
}

function stopPolling() {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    if (animFrame) { cancelAnimationFrame(animFrame); animFrame = null; }
}

async function pollHeatmap() {
    if (!currentFloorId) return;
    try {
        const [hmResp, dbgResp] = await Promise.all([
            fetch(`${API}/floors/${currentFloorId}/heatmap`),
            fetch(`${API}/debug/triangulation`),
        ]);
        heatmapData = await hmResp.json();
        const dbg = await dbgResp.json();

        const dbgEl = document.getElementById('debug-info');
        if (dbgEl) {
            dbgEl.innerHTML = `
                <div><b>${dbg.total_devices}</b> devices located</div>
                <div><b style="color:#c97b4b">${dbg.triangulatable_count}</b> seen by 2+ APs (triangulated)</div>
                <div><b>${dbg.single_ap_count}</b> seen by 1 AP only</div>
                <div style="margin-top:6px;"><a href="/api/v1/debug/triangulation" target="_blank" style="color:var(--accent);text-decoration:none;">view raw &rarr;</a></div>
            `;
        }
    } catch (e) { console.error('Poll failed:', e); }
}

// ─── Engine Health ───
async function checkEngineHealth() {
    try {
        const resp = await fetch(`${API}/health`);
        const data = await resp.json();
        const dot = document.getElementById('engine-status');
        const txt = document.getElementById('engine-status-text');
        if (data.status === 'ok' && data.engine_running) {
            dot.className = 'status-dot online';
            txt.textContent = `${data.total_devices_tracked} devices tracked`;
        } else {
            dot.className = 'status-dot offline';
            txt.textContent = 'Engine offline';
        }
    } catch {
        document.getElementById('engine-status').className = 'status-dot offline';
        document.getElementById('engine-status-text').textContent = 'Disconnected';
    }
}
