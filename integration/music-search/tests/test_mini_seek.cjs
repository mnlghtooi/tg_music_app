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
      stopPropagation() {},
      ...overrides
    };
    for (const handler of this.handlers[name] || []) handler(event);
    return event;
  }
  getBoundingClientRect() { return { width: 200 }; }
  setPointerCapture() {}
}

function create(options = {}) {
  const trigger = new FakeElement();
  const panel = new FakeElement();
  const slider = new FakeElement();
  const elapsed = new FakeElement();
  const total = new FakeElement();
  const closeButton = new FakeElement();
  const audio = new FakeElement();
  audio.duration = 100;
  audio.currentTime = 20;
  let opened = 0;
  const errors = [];
  const mounted = MiniSeek.mount({
    trigger, panel, slider, elapsed, total, closeButton, audio,
    openPlayer: () => { opened++; },
    formatTime: n => String(Math.round(n)),
    onError: message => errors.push(message),
    holdMs: options.holdMs ?? 0
  });
  return { trigger, panel, slider, elapsed, total, closeButton, audio,
    mounted, errors, get opened() { return opened; } };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 10));

test('clamps quick-scrub position, without jumping on initial hold', () => {
  assert.equal(MiniSeek.scrubTarget(20, 50, 200, 100), 45);
  assert.equal(MiniSeek.scrubTarget(20, -200, 200, 100), 0);
  assert.equal(MiniSeek.scrubTarget(90, 200, 200, 100), 100);
  assert.equal(MiniSeek.durationOf({ duration: Infinity }), 0);
  assert.equal(MiniSeek.durationOf({ duration: NaN }), 0);
  assert.equal(MiniSeek.durationOf({ duration: 95 }), 95);
});

test('short tap opens full player; it never changes track position', () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  ui.trigger.fire('pointerup');
  ui.trigger.fire('click');
  assert.equal(ui.opened, 1);
  assert.equal(ui.audio.currentTime, 20);
  assert.equal(ui.panel.hidden, true);
});

test('hold reveals slider, horizontal drag seeks on release without opening full view', async () => {
  const ui = create();
  ui.trigger.fire('pointerdown');
  await tick();
  assert.equal(ui.panel.hidden, false);
  assert.equal(ui.audio.currentTime, 20, 'holding alone must not seek');
  ui.trigger.fire('pointermove', { clientX: 100 });
  assert.equal(Number(ui.slider.value), 45);
  assert.equal(ui.audio.currentTime, 20, 'audio keeps playing at original position while dragging');
  ui.trigger.fire('pointerup', { clientX: 100 });
  assert.equal(ui.audio.currentTime, 45);
  ui.trigger.fire('click');
  assert.equal(ui.opened, 0);
  ui.closeButton.fire('click');
  assert.equal(ui.panel.hidden, true);
});

test('moving before hold threshold cancels gesture and does not seek', async () => {
  const ui = create({ holdMs: 20 });
  ui.trigger.fire('pointerdown');
  ui.trigger.fire('pointermove', { clientX: 80 });
  await new Promise(resolve => setTimeout(resolve, 35));
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
});

test('expanded slider supports direct and keyboard-friendly seeking', () => {
  const ui = create();
  ui.panel.hidden = false;
  ui.slider.value = '67.3';
  ui.slider.fire('input');
  assert.equal(ui.audio.currentTime, 20);
  ui.slider.fire('change');
  assert.equal(ui.audio.currentTime, 67.3);
  ui.audio.fire('emptied');
  assert.equal(ui.panel.hidden, true);
});

test('unknown duration never seeks or opens quick-scrub controls', async () => {
  const ui = create();
  ui.audio.duration = Infinity;
  ui.trigger.fire('pointerdown');
  await tick();
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.audio.currentTime, 20);
  assert.match(ui.errors.join(' '), /загрузки/);
});
