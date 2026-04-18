const TOKEN_KEY = "poker-token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const API = {
  register: (username: string, password: string, display_name?: string) =>
    api<{ access_token: string; user: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name }),
    }),
  login: (username: string, password: string) =>
    api<{ access_token: string; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => api<AuthUser>("/api/auth/me"),
  createRoom: (payload: CreateRoomPayload) =>
    api<{ code: string; name: string }>("/api/rooms", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listRooms: () => api<RoomSummary[]>("/api/rooms"),
  getRoom: (code: string) => api<RoomInfo>(`/api/rooms/${code}`),
  myHands: () => api<HandSummary[]>("/api/hands"),
  handDetail: (id: number) => api<HandDetail>(`/api/hands/${id}`),
};

export type AuthUser = { id: number; username: string; display_name: string };
export type CreateRoomPayload = {
  name: string;
  sb: number;
  bb: number;
  buyin_min: number;
  buyin_max: number;
  max_seats: number;
};
export type RoomSummary = {
  code: string;
  name: string;
  sb: number;
  bb: number;
  seated: number;
  max_seats: number;
  closes_at: string | null;
};
export type StandingsEntry = {
  display_name: string;
  user_id: number | null;
  is_bot: boolean;
  net: number;
};
export type RoomInfo = RoomSummary & {
  buyin_min: number;
  buyin_max: number;
  closed?: boolean;
  final_standings?: StandingsEntry[] | null;
};
export type HandSummary = {
  hand_id: number;
  room_id: number;
  hand_no: number;
  started_at: string | null;
  ended_at: string | null;
  net: number;
  board: any;
  pot_total: number | null;
};
export type HandDetail = HandSummary & {
  button_seat: number;
  sb: number;
  bb: number;
  seats: Record<string, any>;
  winner_summary: any[] | null;
  actions: any[];
  hole_cards: any[];
};
