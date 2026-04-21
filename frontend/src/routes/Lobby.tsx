import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { API, RoomSummary } from "../lib/api";
import { useAuth } from "../store/auth";

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function fmtCountdown(ms: number): string {
  if (ms <= 0) return "已到期";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export default function Lobby() {
  const user = useAuth((s) => s.user);
  const hydrate = useAuth((s) => s.hydrate);
  const logout = useAuth((s) => s.logout);
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [joinCode, setJoinCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showChangePw, setShowChangePw] = useState(false);
  const navigate = useNavigate();
  const now = useNow(1000);

  useEffect(() => {
    const refresh = () => {
      API.listRooms().then(setRooms).catch(() => {});
      API.me().then(hydrate).catch(() => {});
    };
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [hydrate]);

  async function create(payload: any) {
    try {
      const res = await API.createRoom(payload);
      navigate(`/room/${res.code}`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function join() {
    if (!joinCode) return;
    try {
      await API.getRoom(joinCode.toUpperCase());
      navigate(`/room/${joinCode.toUpperCase()}`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <div className="min-h-screen px-4 py-6 max-w-2xl mx-auto">
      <header className="flex items-center justify-between mb-6 flex-wrap gap-2">
        <h1 className="text-xl font-bold">朋友局 · 大厅</h1>
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <span className="opacity-80">{user?.display_name}</span>
          {user?.is_guest && (
            <span className="px-1.5 py-0.5 rounded bg-chip-blue/30 text-chip-blue text-[10px]">游客</span>
          )}
          <span className="px-2 py-1 rounded bg-chip-gold/20 text-chip-gold font-semibold">
            余额 {user?.balance ?? 0}
          </span>
          <Link to="/hands" className="px-2 py-1 rounded bg-black/30">手牌历史</Link>
          {!user?.is_guest && (
            <button onClick={() => setShowChangePw(true)} className="px-2 py-1 rounded bg-black/30">
              改密码
            </button>
          )}
          {user?.is_admin && (
            <Link to="/admin" className="px-2 py-1 rounded bg-red-600/80">管理</Link>
          )}
          <button onClick={logout} className="px-2 py-1 rounded bg-black/30">退出</button>
        </div>
      </header>

      <section className="bg-feltLight rounded-2xl p-4 mb-4">
        <div className="flex items-center gap-2">
          <input value={joinCode} onChange={(e) => setJoinCode(e.target.value)}
                 placeholder="输入房间码" maxLength={8}
                 className="flex-1 rounded px-3 py-2 bg-black/40 uppercase tracking-wider" />
          <button onClick={join} className="bg-chip-gold text-black px-4 py-2 rounded-full font-semibold">加入</button>
        </div>
      </section>

      <section className="bg-feltLight rounded-2xl p-4 mb-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold">我的房间 / 正在进行</h2>
          {!user?.is_guest && (
            <button onClick={() => setShowCreate(!showCreate)}
                    className="text-sm bg-chip-blue px-3 py-1 rounded-full">
              {showCreate ? "取消" : "+ 建房"}
            </button>
          )}
        </div>
        {showCreate && !user?.is_guest && <CreateForm onSubmit={create} />}
        <ul className="mt-3 space-y-2">
          {rooms.length === 0 && <li className="text-sm opacity-70">暂无房间</li>}
          {rooms.map((r) => {
            const closesMs = r.closes_at ? new Date(r.closes_at).getTime() - now : null;
            const urgent = closesMs != null && closesMs < 10 * 60 * 1000;
            return (
              <li key={r.code}>
                <Link to={`/room/${r.code}`} className="flex items-center justify-between bg-black/30 rounded-xl px-3 py-2">
                  <div>
                    <div className="font-medium flex items-center gap-2">
                      {r.name}
                      {r.allow_guest && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-chip-blue/30 text-chip-blue">
                          游客可入
                        </span>
                      )}
                    </div>
                    <div className="text-xs opacity-70">
                      码 {r.code} · {r.sb}/{r.bb}
                      {closesMs != null && (
                        <span className={`ml-2 ${urgent ? "text-red-300" : "text-chip-gold"}`}>
                          · 距关闭 {fmtCountdown(closesMs)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-sm opacity-80">{r.seated}/{r.max_seats}</div>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>

      {err && <div className="text-red-300 text-sm">{err}</div>}

      {showChangePw && <ChangePasswordModal onClose={() => setShowChangePw(false)} />}
    </div>
  );
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const user = useAuth((s) => s.user);
  const setAuth = useAuth((s) => s.setAuth);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (newPw !== newPw2) {
      setErr("两次新密码不一致");
      return;
    }
    if (newPw.length < 6) {
      setErr("新密码至少 6 位");
      return;
    }
    if (newPw === oldPw) {
      setErr("新密码不能与原密码相同");
      return;
    }
    setBusy(true);
    try {
      const res = await API.changePassword(oldPw, newPw);
      // 后端已 bump password_version，必须用新 token 替换本地，否则下一个请求会 401
      if (user) setAuth(res.access_token, user);
      setOk(true);
      setTimeout(onClose, 1500);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-2">
      <form onSubmit={submit} className="bg-feltLight rounded-2xl p-5 w-full max-w-sm space-y-3">
        <div className="font-semibold">修改密码</div>
        <label className="block text-sm">
          原密码
          <input
            type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)}
            required autoFocus
            className="mt-1 w-full rounded px-3 py-2 bg-black/40"
          />
        </label>
        <label className="block text-sm">
          新密码（≥6 位）
          <input
            type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)}
            required minLength={6}
            className="mt-1 w-full rounded px-3 py-2 bg-black/40"
          />
        </label>
        <label className="block text-sm">
          确认新密码
          <input
            type="password" value={newPw2} onChange={(e) => setNewPw2(e.target.value)}
            required minLength={6}
            className="mt-1 w-full rounded px-3 py-2 bg-black/40"
          />
        </label>
        {err && <div className="text-red-300 text-sm">{err}</div>}
        {ok && <div className="text-green-300 text-sm">已修改</div>}
        <div className="flex gap-2">
          <button
            type="button" onClick={onClose}
            className="flex-1 py-2 rounded-full bg-black/40 text-sm"
          >
            取消
          </button>
          <button
            type="submit" disabled={busy || ok}
            className="flex-1 py-2 rounded-full bg-chip-gold text-black font-semibold text-sm disabled:opacity-40"
          >
            {busy ? "提交中…" : ok ? "✓" : "确认"}
          </button>
        </div>
      </form>
    </div>
  );
}

function CreateForm({ onSubmit }: { onSubmit: (p: any) => void }) {
  const [name, setName] = useState("friendly game");
  const [sb, setSb] = useState(1);
  const [bb, setBb] = useState(2);
  const [minBuyin, setMin] = useState(100);
  const [maxBuyin, setMax] = useState(200);
  const [seats, setSeats] = useState(6);
  const [allowGuest, setAllowGuest] = useState(false);
  return (
    <form
      className="grid grid-cols-2 gap-2 mt-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          name, sb, bb,
          buyin_min: minBuyin, buyin_max: maxBuyin,
          max_seats: seats, allow_guest: allowGuest,
        });
      }}
    >
      <label className="col-span-2 text-sm">房间名
        <input value={name} onChange={(e) => setName(e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="text-sm">SB
        <input type="number" value={sb} onChange={(e) => setSb(+e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="text-sm">BB
        <input type="number" value={bb} onChange={(e) => setBb(+e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="text-sm">最小带入
        <input type="number" value={minBuyin} onChange={(e) => setMin(+e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="text-sm">最大带入
        <input type="number" value={maxBuyin} onChange={(e) => setMax(+e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="col-span-2 text-sm">座位数
        <input type="number" min={2} max={9} value={seats} onChange={(e) => setSeats(+e.target.value)}
               className="mt-1 w-full rounded px-2 py-1 bg-black/40" /></label>
      <label className="col-span-2 text-sm flex items-center gap-2 select-none">
        <input type="checkbox" checked={allowGuest} onChange={(e) => setAllowGuest(e.target.checked)} />
        <span>允许游客入座（play-money 桌）</span>
      </label>
      <button className="col-span-2 mt-2 bg-chip-gold text-black py-2 rounded-full font-semibold">创建并进入</button>
    </form>
  );
}
