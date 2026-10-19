/* Long-press scrubbing on the entire mini-player, except playback controls.
 * While dragging, the preview is visual only. Release seeks once and hides it.
 * Full-player controls and audio play/pause state are not touched. */
(function (root) {
  'use strict';
  const HOLD_MS = 350;
  const MOVE_SLOP = 12;
  const DRAG_SLOP = 4;
  const CONTROL_SELECTOR = '#miniPlay, #miniNext, #miniSeekPanel';

  function durationOf(audio) {
    const duration = audio.duration;
    return typeof duration === 'number' && Number.isFinite(duration) && duration > 0 ? duration : 0;
  }
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
  }
  // Relative scrubbing prevents jumps when the user holds at any starting x.
  function scrubTarget(start, deltaX, width, duration) {
    if (!duration || !Number.isFinite(width) || width <= 0) return 0;
    return clamp(start + deltaX * duration / width, 0, duration);
  }

  function mount({ trigger, panel, slider, elapsed, total, audio,
                   openPlayer, formatTime, onError, holdMs = HOLD_MS }) {
    let gesture = null;
    let holdTimer = null;
    let swallowClick = false;
    let swallowTimer = null;

    function time(n) { return formatTime(Math.max(0, n || 0)); }
    function sync() {
      const duration = durationOf(audio);
      slider.disabled = duration === 0;
      slider.max = String(duration || 1);
      total.textContent = time(duration);
      if (!gesture?.active) {
        slider.value = String(clamp(audio.currentTime || 0, 0, duration || 1));
        elapsed.textContent = time(audio.currentTime);
      }
    }
    function close() {
      panel.hidden = true;
      sync();
    }
    function commit(position) {
      const duration = durationOf(audio);
      if (!duration) {
        onError('Перемотка станет доступна после загрузки трека.');
        return;
      }
      try {
        audio.currentTime = clamp(position, 0, duration);
      } catch (_) {
        onError('Не удалось перемотать эту запись.');
      }
    }
    function clearHold() {
      if (holdTimer !== null) clearTimeout(holdTimer);
      holdTimer = null;
    }
    function releaseGesture() {
      if (gesture?.active) {
        try { trigger.releasePointerCapture?.(gesture.id); } catch (_) { /* WebView */ }
      }
      clearHold();
      gesture = null;
      trigger.classList.remove('mini-scrubbing');
    }
    function suppressClick() {
      swallowClick = true;
      clearTimeout(swallowTimer);
      swallowTimer = setTimeout(() => { swallowClick = false; }, 500);
    }
    function isPlaybackControl(event) {
      return Boolean(event.target?.closest?.(CONTROL_SELECTOR));
    }
    function onPointerDown(event) {
      if (isPlaybackControl(event) || event.isPrimary === false ||
          (event.pointerType === 'mouse' && event.button !== 0)) return;
      if (gesture) { releaseGesture(); close(); }
      gesture = {
        id: event.pointerId, x: event.clientX, y: event.clientY,
        start: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
        preview: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
        active: false, moved: false
      };
      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (!gesture) return;
        if (!durationOf(audio)) {
          onError('Дождись загрузки трека — затем можно перемотать.');
          releaseGesture();
          return;
        }
        gesture.active = true;
        trigger.classList.add('mini-scrubbing');
        panel.hidden = false;
        slider.value = String(gesture.preview);
        elapsed.textContent = time(gesture.preview);
        try { trigger.setPointerCapture?.(gesture.id); } catch (_) { /* WebView */ }
      }, holdMs);
    }
    function onPointerMove(event) {
      if (!gesture || event.pointerId !== gesture.id) return;
      if (!gesture.active) {
        // Swiping before hold is not a seek.
        if (Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > MOVE_SLOP) {
          releaseGesture();
        }
        return;
      }
      const duration = durationOf(audio);
      gesture.moved = Math.abs(event.clientX - gesture.x) >= DRAG_SLOP;
      gesture.preview = scrubTarget(
        gesture.start, event.clientX - gesture.x,
        trigger.getBoundingClientRect().width || 1, duration
      );
      slider.value = String(gesture.preview);
      elapsed.textContent = time(gesture.preview);
    }
    function onPointerUp(event) {
      if (!gesture || event.pointerId !== gesture.id) return;
      const wasScrubbing = gesture.active;
      const moved = gesture.moved;
      const target = gesture.preview;
      releaseGesture();
      if (wasScrubbing) {
        suppressClick(); // Prevent opening the full player on release.
        if (moved) commit(target);
        close(); // Preview never stays open after the user lifts their finger.
      }
    }
    function onPointerCancel(event) {
      if (!gesture || event.pointerId !== gesture.id) return;
      const wasScrubbing = gesture.active;
      releaseGesture();
      if (wasScrubbing) suppressClick();
      close(); // Cancelling never seeks.
    }

    trigger.addEventListener('pointerdown', onPointerDown);
    trigger.addEventListener('pointermove', onPointerMove);
    trigger.addEventListener('pointerup', onPointerUp);
    trigger.addEventListener('pointercancel', onPointerCancel);
    trigger.addEventListener('lostpointercapture', event => {
      if (gesture && event.pointerId === gesture.id) onPointerCancel(event);
    });
    trigger.addEventListener('contextmenu', event => event.preventDefault());
    trigger.addEventListener('selectstart', event => event.preventDefault());
    trigger.addEventListener('dragstart', event => event.preventDefault());
    trigger.addEventListener('click', event => {
      if (swallowClick) {
        swallowClick = false;
        clearTimeout(swallowTimer);
        event.preventDefault();
        event.stopPropagation();
      } else if (!isPlaybackControl(event)) {
        openPlayer();
      }
    });

    audio.addEventListener('emptied', () => { releaseGesture(); close(); });
    audio.addEventListener('durationchange', sync);
    audio.addEventListener('loadedmetadata', sync);
    audio.addEventListener('timeupdate', sync);
    sync();
    return { close, sync };
  }

  const api = { mount, durationOf, scrubTarget };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MiniSeek = api;
})(typeof window !== 'undefined' ? window : globalThis);
