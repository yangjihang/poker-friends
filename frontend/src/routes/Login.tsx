import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { API } from "../lib/api";
import { useAuth } from "../store/auth";

export default function Login() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setAuth = useAuth((s) => s.setAuth);
  const navigate = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res =
        mode === "login"
          ? await API.login(username, password)
          : await API.register(username, password, inviteCode, displayName || undefined);
      setAuth(res.access_token, res.user);
      navigate("/");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function guestLogin() {
    setErr(null);
    setBusy(true);
    try {
      const res = await API.guest();
      setAuth(res.access_token, res.user);
      navigate("/");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-feltLight rounded-2xl p-6 shadow-xl space-y-4">
        <h1 className="text-2xl font-bold text-center">朋友局</h1>
        <div className="flex gap-2 justify-center">
          <button type="button" onClick={() => setMode("login")}
                  className={`px-3 py-1 rounded-full ${mode === "login" ? "bg-chip-gold text-black" : "bg-black/30"}`}>登录</button>
          <button type="button" onClick={() => setMode("register")}
                  className={`px-3 py-1 rounded-full ${mode === "register" ? "bg-chip-gold text-black" : "bg-black/30"}`}>注册</button>
        </div>
        <label className="block text-sm">
          账号
          <input value={username} onChange={(e) => setUsername(e.target.value)}
                 className="mt-1 w-full rounded px-3 py-2 bg-black/40" autoComplete="username" />
        </label>
        <label className="block text-sm">
          密码
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 className="mt-1 w-full rounded px-3 py-2 bg-black/40" autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </label>
        {mode === "register" && (
          <>
            <label className="block text-sm">
              邀请码
              <input
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
                required
                autoFocus
                className="mt-1 w-full rounded px-3 py-2 bg-black/40 tracking-widest font-mono"
              />
            </label>
            <label className="block text-sm">
              昵称（可选）
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
                     className="mt-1 w-full rounded px-3 py-2 bg-black/40" />
            </label>
            <div className="text-xs text-white/60">注册成功后赠送 20000 筹码</div>
          </>
        )}
        {err && <div className="text-red-300 text-sm">{err}</div>}
        <button disabled={busy} className="w-full bg-chip-gold text-black py-2 rounded-full font-semibold disabled:opacity-60">
          {busy ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
        </button>
        <div className="relative py-1">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-white/20" />
          </div>
          <div className="relative flex justify-center">
            <span className="bg-feltLight px-2 text-xs text-white/50">或</span>
          </div>
        </div>
        <button
          type="button"
          onClick={guestLogin}
          disabled={busy}
          className="w-full bg-black/40 py-2 rounded-full text-sm disabled:opacity-60"
        >
          游客试玩（仅 play-money 桌）
        </button>
      </form>
    </div>
  );
}
