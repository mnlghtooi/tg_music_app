/* Shared track-availability decisions for catalog UI and saved-library playback.
 * Legacy backups may contain preview:true for full tracks <=30s; that legacy
 * flag is not reliable proof of an excerpt and must never erase a saved item. */
(function (root) {
  'use strict';
  const VALID = new Set(['playable', 'preview_only', 'blocked', 'unknown', 'temporary_error']);
  function state(track) {
    if (!track || typeof track !== 'object') return 'unknown';
    if (track.unavailable === true) return 'blocked';
    if (VALID.has(track.availability)) return track.availability;
    // No authoritative provider policy is stored in older library backups.
    return 'unknown';
  }
  function blockedFromPlayback(track) {
    return ['blocked', 'preview_only', 'temporary_error'].includes(state(track));
  }
  function visibleInCatalog(item) {
    if (!item || typeof item !== 'object') return false;
    if (item.kind && item.kind !== 'track') return true;
    return !blockedFromPlayback(item);
  }
  function catalogView(items) {
    const visible = [], counts = { blocked: 0, preview_only: 0, temporary_error: 0 };
    for (const item of items || []) {
      if (visibleInCatalog(item)) visible.push(item);
      else { const value = state(item); counts[value] = (counts[value] || 0) + 1; }
    }
    return { visible, counts, hidden: (items || []).length - visible.length };
  }
  function playableQueue(items) {
    return (items || []).filter(t => (!t.kind || t.kind === 'track') && !blockedFromPlayback(t));
  }
  function failureMessage(track) {
    switch (state(track)) {
      case 'blocked': return 'Этот трек недоступен у источника. Текущая музыка продолжает играть.';
      case 'preview_only': return 'Для этой записи доступен только фрагмент. Текущая музыка продолжает играть.';
      case 'temporary_error': return 'Источник временно недоступен. Попробуй позднее.';
      default: return '';
    }
  }
  const api = { state, blockedFromPlayback, visibleInCatalog, catalogView, playableQueue, failureMessage };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.TrackAvailability = api;
})(typeof window !== 'undefined' ? window : globalThis);
