const test = require('node:test');
const assert = require('node:assert/strict');
const MiniSeek = require('../mini_seek.js');

class FakeElement {
  constructor() {
    this.handlers = {};
    this.hidden = true;
    this.value = '0';
    this.disabled = false;
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(name, listener) {
    (this.handlers[name] ||= []).push(listener);
  }
  fire(name, overrides = {}) {
    const event = {
      pointerId: 1, clientX: 50, clientY: 10, pointerType: 'touch',
      isPrimary: true, button: 0,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      ...overrides
    };
    for (const handler of this.handlers[name] || []) handler(event);
    return event;
  }
  getBoundingClientRect() { return { width: 200 }; }
  setPointerCapture() {}
  // Mimic the browser firing a synchronous lostcapture during release:
  // releaseGesture() must clear its state before this callback.
  releasePointerCapture(id) { this.fire('lostpointercapture', { pointerId: id }); }
}

function create(options = {}) {
  const trigger = new FakeElement();
  const panel = new FakeElement();
  const slider = new FakeElement();
  const elapsed = new FakeElement();
  const total = new FakeElement();
  const audio = new FakeElement();
  audio.duration = 100;
  audio.currentTime = 20;
  let opened = 0;
  const errors = [];
  const mounted = MiniSeek.mount({
    trigger, panel, slider, elapsed, total, audio,
    openPlayer: () => { opened++; },
    formatTime: n => String(Math.round(n)),
    onError: message => errors.push(message),
    holdMs: options.holdMs ?? 0
  });
  return { trigger, panel, slider, elapsed, total, audio,
    mounted, errors, get opened() { return opened; } };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const controlTarget = id => ({
  closest(selector) { return selector.includes(id) ? { id } : null; }
});

test('relative scrubbing clamps at track ends', () => {
  assert.equal(MiniSeek.scrubTarget(20, 50, 200, 100), 45);
  assert.equal(MiniSeek.scrubTarget(20, -200, 200, 100), 0);
  assert.equal(MiniSeek.scrubTarget(90, 200, 200, 100), 100);
  assert.equal(MiniSeek.durationOf({ duration: Infinity }), 0);
  assert.equal(MiniSeek.durationOf({ duration: NaN }), 0);
  assert.equal(MiniSeek.durationOf({ duration: 95 }), 95);
});

test('short tap on mini-player track area opens full player, no seek', () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  ui.trigger.fire('pointerup');
  ui.trigger.fire('click');
  assert.equal(ui.opened, 1);
  assert.equal(ui.audio.currentTime, 20);
  assert.equal(ui.panel.hidden, true);
});

test('holding anywhere on mini-player reveals preview, drag seeks once and auto-hides on release', async () => {
  const ui = create();
  ui.trigger.fire('pointerdown', { clientX: 90 });
  await tick();
  assert.equal(ui.panel.hidden, false);
  assert.equal(ui.audio.currentTime, 20, 'holding must not seek');
  ui.trigger.fire('pointermove', { clientX: 140 });
  assert.equal(Number(ui.slider.value), 45);
  assert.equal(ui.audio.currentTime, 20, 'dragging only changes preview');
  ui.trigger.fire('pointerup', { clientX: 140 });
  assert.equal(ui.audio.currentTime, 45);
  assert.equal(ui.panel.hidden, true, 'release automatically closes preview');
  ui.trigger.fire('click');
  assert.equal(ui.opened, 0, 'release must not open full view');
});

test('holding without movement closes preview and does not jump in track', async () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  await tick();
  assert.equal(ui.panel.hidden, false);
  ui.trigger.fire('pointerup');
  ui.trigger.fire('click');
  assert.equal(ui.audio.currentTime, 20);
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.opened, 0);
});

test('pre-hold movement cancels gesture without seeking', async () => {
  const ui = create({ holdMs: 20 });
  ui.trigger.fire('pointerdown');
  ui.trigger.fire('pointermove', { clientX: 80 });
  await new Promise(resolve => setTimeout(resolve, 35));
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
});

test('pointer cancel and lost capture hide preview without seeking', async () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  await tick();
  ui.trigger.fire('pointermove', { clientX: 150 });
  ui.trigger.fire('pointercancel');
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
  ui.trigger.fire('pointerdown');
  await tick();
  ui.trigger.fire('lostpointercapture');
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
});

test('Play/Next/preview controls are excluded from long press and click-to-open', async () => {
  const ui = create();
  for (const id of ['#miniPlay', '#miniNext', '#miniSeekPanel']) {
    const target = controlTarget(id);
    ui.trigger.fire('pointerdown', { target });
    await tick();
    ui.trigger.fire('pointerup', { target });
    ui.trigger.fire('click', { target });
    assert.equal(ui.panel.hidden, true);
    assert.equal(ui.opened, 0);
    assert.equal(ui.audio.currentTime, 20);
  }
});

test('context menu, selection and native drag are suppressed within the mini-player', () => {
  const ui = create();
  for (const event of ['contextmenu', 'selectstart', 'dragstart']) {
    assert.equal(ui.trigger.fire(event).defaultPrevented, true);
  }
});

test('track change closes preview and cancels ongoing gesture', async () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  await tick();
  ui.trigger.fire('pointermove', { clientX: 130 });
  ui.audio.fire('emptied');
  assert.equal(ui.panel.hidden, true);
  ui.trigger.fire('pointerup');
  assert.equal(ui.audio.currentTime, 20);
});

test('unknown media duration cannot seek', async () => {
  const ui = create();
  ui.audio.duration = Infinity;
  ui.trigger.fire('pointerdown');
  await tick();
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
  assert.match(ui.errors.join(' '), /загрузки/);
});
