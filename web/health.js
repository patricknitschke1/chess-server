// The operator's heartbeat, readable from across the room.

const STALE_TICK_MS = 5000;

class HealthBanner {
  constructor(element) {
    this.element = element;
    this.lastTickMs = null;
    this.hide();
  }

  update(data) {
    this.lastTickMs = data.last_tick_age_ms;
    if (data.last_tick_age_ms > STALE_TICK_MS) {
      this.show(`SERVER TICK STALLED — last tick ${data.last_tick_age_ms} ms ago`);
    } else {
      this.hide();
    }
  }

  disconnected() {
    this.show('DISCONNECTED FROM SERVER — reconnecting');
  }

  show(message) {
    this.element.textContent = message;
    this.element.classList.remove('hidden');
  }

  hide() {
    this.element.textContent = '';
    this.element.classList.add('hidden');
  }
}
