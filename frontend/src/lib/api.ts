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
  register: (username: string, password: string, invite_code: string, display_name?: string) =>
    api<{ access_token: string; user: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, invite_code, display_name }),
    }),
  login: (username: string, password: string) =>
    api<{ access_token: string; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => api<AuthUser>("/api/auth/me"),
  changePassword: (old_password: string, new_password: string) =>
    api<{ ok: true; access_token: string }>("/api/auth/change_password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    }),
  createRoom: (payload: CreateRoomPayload) =>
    api<{ code: string; name: string }>("/api/rooms", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listRooms: () => api<RoomSummary[]>("/api/rooms"),
  getRoom: (code: string) => api<RoomInfo>(`/api/rooms/${code}`),
  myHands: () => api<HandSummary[]>("/api/hands"),
  handDetail: (id: number) => api<HandDetail>(`/api/hands/${id}`),

  // ----- admin -----
  adminListUsers: () => api<AdminUser[]>("/api/admin/users"),
  adminGetUser: (id: number) => api<AdminUser>(`/api/admin/users/${id}`),
  adminUserHands: (id: number, limit = 100) =>
    api<HandSummary[]>(`/api/admin/users/${id}/hands?limit=${limit}`),
  adminUserLedger: (id: number, limit = 200) =>
    api<LedgerEntry[]>(`/api/admin/users/${id}/ledger?limit=${limit}`),
  adminTopup: (id: number, amount: number, note?: string) =>
    api<{ ok: true; user_id: number; balance: number; ledger_id: number }>(
      `/api/admin/users/${id}/topup`,
      {
        method: "POST",
        body: JSON.stringify({ amount, note }),
      }
    ),
  adminResetPassword: (id: number, new_password?: string) =>
    api<{ ok: true; new_password: string }>(`/api/admin/users/${id}/reset_password`, {
      method: "POST",
      body: JSON.stringify({ new_password: new_password || null }),
    }),
  adminListInvites: () => api<InviteCode[]>("/api/admin/invite_codes"),
  adminGenInvites: (count: number) =>
    api<{ codes: string[] }>("/api/admin/invite_codes", {
      method: "POST",
      body: JSON.stringify({ count }),
    }),
  adminPendingCashouts: () => api<PendingCashout[]>("/api/admin/pending_cashouts"),
  adminAckPendingCashout: (id: number, matched_ledger_id: number) =>
    api<{ ok: true }>(`/api/admin/pending_cashouts/${id}/ack`, {
      method: "POST",
      body: JSON.stringify({ matched_ledger_id }),
    }),
  adminHandDetail: (id: number) => api<HandDetail>(`/api/admin/hands/${id}`),
};

export type AuthUser = {
  id: number;
  username: string;
  display_name: string;
  balance: number;
  is_admin: boolean;
};
export type AdminUser = AuthUser & { created_at: string | null };
export type LedgerEntry = {
  id: number;
  type: string;
  amount: number;
  balance_after: number;
  room_id: number | null;
  hand_id: number | null;
  note: string | null;
  actor_user_id: number | null;
  created_at: string | null;
};
export type InviteCode = {
  id: number;
  code: string;
  created_by: number | null;
  used_by: number | null;
  created_at: string | null;
  used_at: string | null;
};
export type PendingCashout = {
  id: number;
  user_id: number;
  username: string | null;
  display_name: string | null;
  amount: number;
  room_id: number | null;
  note: string | null;
  created_at: string | null;
};
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
  created_by?: number;
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
