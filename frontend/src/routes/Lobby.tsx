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
  const logout = useAuth((s) => s.logout);
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [joinCode, setJoinCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const navigate = useNavigate();
  const now = useNow(1000);

  useEffect(() => {
    API.listRooms().then(setRooms).catch(() => {});
    const t = setInterval(() => API.listRooms().then(setRooms).catch(() => {}), 4000);
    return () => clearInterval(t);
  }, []);

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
      <header className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold">朋友局 · 大厅</h1>
        <div className="flex items-center gap-2 text-sm">
          <span className="opacity-80">{user?.display_name}</span>
          <Link to="/hands" className="px-2 py-1 rounded bg-black/30">手牌历史</Link>
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
          <button onClick={() => setShowCreate(!showCreate)}
                  className="text-sm bg-chip-blue px-3 py-1 rounded-full">
            {showCreate ? "取消" : "+ 建房"}
          </button>
        </div>
        {showCreate && <CreateForm onSubmit={create} />}
        <ul className="mt-3 space-y-2">
          {rooms.length === 0 && <li className="text-sm opacity-70">暂无房间</li>}
          {rooms.map((r) => {
            const closesMs = r.closes_at ? new Date(r.closes_at).getTime() - now : null;
            const urgent = closesMs != null && closesMs < 10 * 60 * 1000;
            return (
              <li key={r.code}>
                <Link to={`/room/${r.code}`} className="flex items-center justify-between bg-black/30 rounded-xl px-3 py-2">
                  <div>
                    <div className="font-medium">{r.name}</div>
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
  return (
    <form
      className="grid grid-cols-2 gap-2 mt-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ name, sb, bb, buyin_min: minBuyin, buyin_max: maxBuyin, max_seats: seats });
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
      <button className="col-span-2 mt-2 bg-chip-gold text-black py-2 rounded-full font-semibold">创建并进入</button>
    </form>
  );
}
