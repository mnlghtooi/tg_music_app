const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const status = require('../track_availability.js');
const transfer = require('../library_transfer.js');

const track = (id, availability, extra = {}) => ({
  id, kind: 'track', source: 'soundcloud',
  title: 'Track ' + id, artist: 'Artist', duration: 200,
  ...(availability ? { availability } : {}), ...extra
});
const backup = items => ({
  format: 'musiclab-library', version: 1,
  library: { likes: items, dislikes: [], playlists: [{ id: 'p', name: 'Songs', tracks: items }] }
});

test('search catalog hides only confirmed unavailable and preview-only audio', () => {
  const items = [
    track('full', 'playable'),
    track('short', 'playable', { duration: 19 }),
    track('preview', 'preview_only', { duration: 30, preview: true }),
    track('blocked', 'blocked', { unavailable: true }),
    track('unknown', 'unknown', { duration: 0 }),
    track('network', 'temporary_error'),
    { id: 'a', kind: 'artist', source: 'soundcloud', title: 'Artist' },
    { id: 'b', kind: 'album', source: 'bandcamp', title: 'Album' }
  ];
  const view = status.catalogView(items);
  assert.deepEqual(view.visible.map(t => t.id), ['full', 'short', 'unknown', 'a', 'b']);
  assert.equal(view.hidden, 3);
  assert.deepEqual(view.counts, { blocked: 1, preview_only: 1, temporary_error: 1 });
  assert.deepEqual(status.playableQueue(view.visible).map(t => t.id), ['full', 'short', 'unknown']);
  assert.equal(items.length, 8, 'source list is never mutated');
});

test('empty displayed page retains source results for pagination and explanatory status', () => {
  const entries = [track('p', 'preview_only'), track('b', 'blocked')];
  const { visible, hidden } = status.catalogView(entries);
  assert.equal(visible.length, 0);
  assert.equal(hidden, 2);
  assert.equal(entries.length, 2, 'pagination can use original result size');
});

test('old false preview flags in existing library do not block full short songs', () => {
  const legacy = track('old', null, { duration: 20, preview: true });
  assert.equal(status.state(legacy), 'unknown');
  assert.ok(status.visibleInCatalog(legacy));
  assert.equal(status.failureMessage(legacy), '');
  const imported = transfer.validate(backup([legacy]));
  assert.equal(imported.likes[0].availability, 'unknown');
  assert.equal(imported.likes[0].preview, false);
  assert.equal(imported.playlists[0].tracks[0].id, 'old');
});

test('explicitly gated/preview saved tracks remain in backup but not playback queue', () => {
  const items = [
    track('good', 'playable', { duration: 16 }),
    track('bad', 'blocked', { unavailable: true }),
    track('sample', 'preview_only', { preview: true }),
    track('unknown', 'unknown')
  ];
  const imported = transfer.validate(backup(items));
  assert.deepEqual(imported.playlists[0].tracks.map(t => t.id), items.map(t => t.id));
  assert.deepEqual(status.playableQueue(imported.playlists[0].tracks).map(t => t.id), ['good', 'unknown']);
  assert.equal(imported.likes[2].availability, 'preview_only');
  assert.equal(imported.likes[2].preview, true);
  assert.match(status.failureMessage(items[1]), /продолжает играть/);
  assert.match(status.failureMessage(items[2]), /фрагмент/);
});

test('explicit availability always validates against allowed values', () => {
  const future = track('future', 'admin', { unavailable: false });
  const imported = transfer.validate(backup([future]));
  assert.equal(imported.likes[0].availability, 'unknown');
  const contradictory = track('blocked', 'playable', { unavailable: true });
  assert.equal(status.state(contradictory), 'blocked');
  assert.equal(transfer.validate(backup([contradictory])).likes[0].availability, 'blocked');
});

test('HTML loads availability helper before inline player and uses guarded playback', () => {
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const beforeInlineScript = html.slice(0, html.indexOf('<script>'));
  assert.match(beforeInlineScript, /src="\/track_availability\.js"/);
  assert.match(html, /const availability=window\.TrackAvailability;/);
  assert.match(html, /availability\.catalogView\(items\)/);
  assert.match(html, /availability\.playableQueue\(items\)/);
  assert.match(html, /const problem=availability\.failureMessage\(tracks\[nextIndex\]\)/);
  assert.match(html, /Скрыто:/);
  assert.doesNotMatch(html, /duration\s*<=\s*30/);
});
