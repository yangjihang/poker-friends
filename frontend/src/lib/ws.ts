export type GameState = {
  room: any;
  engine: any | null;
  your_hole_cards: string[] | null;
  your_best_hand: string | null;
  your_seat_idx: number | null;
};

export type ServerMessage =
  | { type: "state"; room: any; engine: any | null; your_hole_cards: string[] | null; your_best_hand: string | null; your_seat_idx: number | null }
  | { type: "event"; kind: string; data: any }
  | { type: "hand_end"; data: any }
  | { type: "room_closed"; data: any }
  | { type: "chat"; from: string; text: string }
  | { type: "balance_update"; balance: number }
  | { type: "error"; msg: string };

export class GameSocket {
  private ws: WebSocket | null = null;
  private pendingSend: any[] = [];
  private closed = false;

  constructor(
    private code: string,
    private token: string,
    private onMessage: (m: ServerMessage) => void,
    private onOpen?: () => void,
  ) {}

  connect() {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    this.ws = new WebSocket(`${proto}://${host}/ws/room/${this.code}?token=${encodeURIComponent(this.token)}`);
    this.ws.onopen = () => {
      this.pendingSend.forEach((m) => this.ws?.send(JSON.stringify(m)));
      this.pendingSend = [];
      this.onOpen?.();
    };
    this.ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    };
    this.ws.onclose = () => {
      if (!this.closed) setTimeout(() => this.connect(), 1500);
    };
  }

  send(msg: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    } else {
      this.pendingSend.push(msg);
    }
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}
