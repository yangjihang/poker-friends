import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AdminUser,
  API,
  HandSummary,
  InviteCode,
  LedgerEntry,
  PendingCashout,
} from "../lib/api";
import { useAuth } from "../store/auth";

type Tab = "users" | "invites" | "pending";

export default function Admin() {
  const me = useAuth((s) => s.user);
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("users");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (me && !me.is_admin) navigate("/");
  }, [me, navigate]);

  if (!me?.is_admin) return null;

  return (
    <div className="min-h-screen px-4 py-6 max-w-4xl mx-auto">
      <header className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h1 className="text-xl font-bold">管理后台</h1>
        <Link to="/" className="px-3 py-1 rounded bg-black/30 text-sm">返回大厅</Link>
      </header>

      <div className="flex gap-2 mb-4 flex-wrap">
        <TabBtn active={tab === "users"} onClick={() => setTab("users")}>用户 & 余额</TabBtn>
        <TabBtn active={tab === "invites"} onClick={() => setTab("invites")}>邀请码</TabBtn>
        <TabBtn active={tab === "pending"} onClick={() => setTab("pending")}>待处理 Cashout</TabBtn>
      </div>

      {err && <div className="text-red-300 text-sm mb-2">{err}</div>}

      {tab === "users" && <UsersPane onErr={setErr} />}
      {tab === "invites" && <InvitesPane onErr={setErr} />}
      {tab === "pending" && <PendingCashoutPane onErr={setErr} />}
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: any }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 rounded-full text-sm ${active ? "bg-chip-gold text-black" : "bg-black/30"}`}
    >
      {children}
    </button>
  );
}

function UsersPane({ onErr }: { onErr: (s: string | null) => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sel, setSel] = useState<AdminUser | null>(null);
  const [q, setQ] = useState("");

  async function refresh() {
    try {
      const rows = await API.adminListUsers();
      setUsers(rows);
      onErr(null);
    } catch (e) {
      onErr((e as Error).message);
    }
  }

  useEffect(() => { refresh(); }, []);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return users;
    return users.filter(
      (u) => u.username.toLowerCase().includes(kw) || u.display_name.toLowerCase().includes(kw)
    );
  }, [users, q]);

  return (
    <div>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="搜索用户名 / 昵称"
        className="mb-3 w-full rounded px-3 py-2 bg-black/40 text-sm"
      />
      <div className="bg-feltLight rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-black/40 text-white/70">
            <tr>
              <th className="text-left px-3 py-2">账号</th>
              <th className="text-left px-3 py-2">昵称</th>
              <th className="text-right px-3 py-2">余额</th>
              <th className="text-right px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((u) => (
              <tr key={u.id} className="border-t border-white/5">
                <td className="px-3 py-2">
                  {u.username}
                  {u.is_admin && <span className="ml-1 text-xs text-red-300">[admin]</span>}
                </td>
                <td className="px-3 py-2">{u.display_name}</td>
                <td className="px-3 py-2 text-right font-mono">{u.balance}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => setSel(u)}
                    className="text-xs px-2 py-1 rounded bg-chip-blue"
                  >
                    查看 / 充值
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-white/60">无匹配用户</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {sel && (
        <UserDetailPanel
          user={sel}
          onClose={() => setSel(null)}
          onChanged={() => {
            refresh();
            // 重新拉一下详情
            API.adminGetUser(sel.id).then(setSel).catch(() => {});
          }}
          onErr={onErr}
        />
      )}
    </div>
  );
}

function UserDetailPanel({
  user,
  onClose,
  onChanged,
  onErr,
}: {
  user: AdminUser;
  onClose: () => void;
  onChanged: () => void;
  onErr: (s: string | null) => void;
}) {
  const [hands, setHands] = useState<HandSummary[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [amount, setAmount] = useState<number>(0);
  const [note, setNote] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [resetPw, setResetPw] = useState<string | null>(null);
  const [lastTopupLedger, setLastTopupLedger] = useState<number | null>(null);

  async function load() {
    try {
      const [h, l] = await Promise.all([
        API.adminUserHands(user.id, 100),
        API.adminUserLedger(user.id, 200),
      ]);
      setHands(h);
      setLedger(l);
    } catch (e) {
      onErr((e as Error).message);
    }
  }

  useEffect(() => { load(); }, [user.id]);

  async function submitTopup() {
    if (!amount) return;
    setBusy(true);
    try {
      const res = await API.adminTopup(user.id, amount, note || undefined);
      setLastTopupLedger(res.ledger_id);
      setAmount(0);
      setNote("");
      onErr(null);
      onChanged();
      await load();
    } catch (e) {
      onErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function doResetPassword() {
    if (!confirm(`确定重置 ${user.username} 的密码？将生成一个随机临时密码。`)) return;
    setBusy(true);
    try {
      const res = await API.adminResetPassword(user.id);
      setResetPw(res.new_password);
      onErr(null);
    } catch (e) {
      onErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-2">
      <div className="bg-feltLight rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-auto p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="font-bold text-lg">{user.display_name}</div>
            <div className="text-xs opacity-70">@{user.username}（id={user.id}）</div>
          </div>
          <button onClick={onClose} className="px-3 py-1 rounded bg-black/40 text-sm">关闭</button>
        </div>

        <div className="mb-4 p-3 bg-black/30 rounded">
          <div className="text-xs opacity-70">当前余额</div>
          <div className="text-2xl font-bold text-chip-gold">{user.balance}</div>
        </div>

        <div className="mb-4 p-3 bg-black/30 rounded">
          <div className="font-semibold mb-2 text-sm">充值 / 调整</div>
          <div className="grid grid-cols-[1fr_2fr_auto] gap-2 items-start">
            <input
              type="number"
              value={amount}
              onChange={(e) => setAmount(+e.target.value)}
              placeholder="金额（正=充值，负=扣款）"
              className="rounded px-2 py-1 bg-black/40 text-sm"
            />
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="备注（可选）"
              className="rounded px-2 py-1 bg-black/40 text-sm"
              maxLength={200}
            />
            <button
              onClick={submitTopup}
              disabled={busy || amount === 0}
              className="px-3 py-1 bg-chip-gold text-black rounded text-sm disabled:opacity-40"
            >
              {busy ? "…" : "提交"}
            </button>
          </div>
          {lastTopupLedger !== null && (
            <div className="mt-2 text-xs bg-black/50 p-2 rounded">
              本次 ledger id：
              <span className="text-chip-gold font-mono select-all">{lastTopupLedger}</span>
              <span className="text-white/50 ml-2">（如果是为了 ack pending cashout，复制此 id）</span>
            </div>
          )}
        </div>

        <div className="mb-4 p-3 bg-black/30 rounded">
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold text-sm">重置密码</div>
            <button
              onClick={doResetPassword}
              disabled={busy}
              className="text-xs px-3 py-1 bg-red-700 rounded disabled:opacity-40"
            >
              生成新临时密码
            </button>
          </div>
          {resetPw && (
            <div className="text-xs bg-black/50 p-2 rounded font-mono select-all break-all">
              新密码：<span className="text-chip-gold">{resetPw}</span>
              <div className="text-[10px] text-white/50 mt-1">发给用户，提醒登录后自行修改</div>
            </div>
          )}
        </div>

        <div className="mb-4">
          <div className="font-semibold mb-2 text-sm">资金流水（最近 200 条）</div>
          <div className="bg-black/30 rounded max-h-64 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-black/80 text-white/70">
                <tr>
                  <th className="text-left px-2 py-1">时间</th>
                  <th className="text-left px-2 py-1">类型</th>
                  <th className="text-right px-2 py-1">金额</th>
                  <th className="text-right px-2 py-1">余额</th>
                  <th className="text-left px-2 py-1">备注</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.id} className="border-t border-white/5">
                    <td className="px-2 py-1 text-white/60 whitespace-nowrap">
                      {e.created_at ? new Date(e.created_at).toLocaleString() : "-"}
                    </td>
                    <td className="px-2 py-1"><TypeTag t={e.type} /></td>
                    <td className={`px-2 py-1 text-right font-mono ${e.amount >= 0 ? "text-green-300" : "text-red-300"}`}>
                      {e.amount >= 0 ? `+${e.amount}` : e.amount}
                    </td>
                    <td className="px-2 py-1 text-right font-mono opacity-80">{e.balance_after}</td>
                    <td className="px-2 py-1 opacity-70">{e.note ?? ""}</td>
                  </tr>
                ))}
                {ledger.length === 0 && (
                  <tr><td colSpan={5} className="text-center py-3 text-white/60">暂无流水</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <div className="font-semibold mb-2 text-sm">手牌历史（最近 100 手）</div>
          <div className="bg-black/30 rounded max-h-64 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-black/80 text-white/70">
                <tr>
                  <th className="text-left px-2 py-1">时间</th>
                  <th className="text-left px-2 py-1">房间</th>
                  <th className="text-right px-2 py-1">本手净</th>
                  <th className="text-right px-2 py-1">底池</th>
                </tr>
              </thead>
              <tbody>
                {hands.map((h) => (
                  <tr key={h.hand_id} className="border-t border-white/5">
                    <td className="px-2 py-1 text-white/60 whitespace-nowrap">
                      {h.ended_at ? new Date(h.ended_at).toLocaleString() : "-"}
                    </td>
                    <td className="px-2 py-1">room#{h.room_id} 第{h.hand_no}手</td>
                    <td className={`px-2 py-1 text-right font-mono ${h.net >= 0 ? "text-green-300" : "text-red-300"}`}>
                      {h.net >= 0 ? `+${h.net}` : h.net}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">{h.pot_total ?? "-"}</td>
                  </tr>
                ))}
                {hands.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-3 text-white/60">暂无手牌</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function TypeTag({ t }: { t: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    register_bonus: { label: "注册奖励", cls: "bg-green-700" },
    admin_topup: { label: "管理员调整", cls: "bg-blue-700" },
    buyin_lock: { label: "入桌质押", cls: "bg-amber-700" },
    room_cashout: { label: "关桌结算", cls: "bg-purple-700" },
  };
  const info = map[t] ?? { label: t, cls: "bg-gray-600" };
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${info.cls}`}>{info.label}</span>;
}

function PendingCashoutPane({ onErr }: { onErr: (s: string | null) => void }) {
  const [rows, setRows] = useState<PendingCashout[]>([]);

  async function refresh() {
    try {
      setRows(await API.adminPendingCashouts());
      onErr(null);
    } catch (e) {
      onErr((e as Error).message);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function ack(id: number) {
    const raw = prompt(
      "输入刚才手动 topup 的 ledger id（作为补偿凭证）。\n" +
      "流程：先在\"用户 & 余额\"里给该用户 +金额 topup，记下返回的 ledger 条目 id，再来这里 ack。"
    );
    if (!raw) return;
    const mid = Number(raw);
    if (!Number.isInteger(mid) || mid < 1) {
      onErr("请输入正整数 ledger id");
      return;
    }
    try {
      await API.adminAckPendingCashout(id, mid);
      await refresh();
    } catch (e) {
      onErr((e as Error).message);
    }
  }

  return (
    <div>
      <div className="text-xs text-white/60 mb-2">
        stand 时 DB 写失败遗留的 cashout 记录。
        确认处理前先在"用户 & 余额"里给对应用户补 topup，然后回来点"已处理"把这条标为 acked。
      </div>
      <div className="bg-feltLight rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-black/40 text-white/70">
            <tr>
              <th className="text-left px-3 py-2">时间</th>
              <th className="text-left px-3 py-2">用户</th>
              <th className="text-right px-3 py-2">金额</th>
              <th className="text-left px-3 py-2">房间 id</th>
              <th className="text-left px-3 py-2">备注</th>
              <th className="text-right px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-white/5">
                <td className="px-3 py-2 text-white/60 whitespace-nowrap">
                  {r.created_at ? new Date(r.created_at).toLocaleString() : "-"}
                </td>
                <td className="px-3 py-2">
                  {r.display_name ?? `user#${r.user_id}`}
                  <span className="text-white/40 text-xs ml-1">@{r.username}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-amber-300">+{r.amount}</td>
                <td className="px-3 py-2 text-white/60">{r.room_id}</td>
                <td className="px-3 py-2 text-xs">{r.note}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => ack(r.id)}
                    className="text-xs px-2 py-1 rounded bg-green-700"
                  >
                    标为已处理
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-white/60">
                  没有待处理的 cashout（全都结算正常）
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function InvitesPane({ onErr }: { onErr: (s: string | null) => void }) {
  const [codes, setCodes] = useState<InviteCode[]>([]);
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<string[]>([]);

  async function refresh() {
    try {
      setCodes(await API.adminListInvites());
      onErr(null);
    } catch (e) {
      onErr((e as Error).message);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function generate() {
    setBusy(true);
    try {
      const res = await API.adminGenInvites(count);
      setRecent(res.codes);
      await refresh();
    } catch (e) {
      onErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const unused = codes.filter((c) => !c.used_by).length;

  return (
    <div>
      <div className="bg-feltLight rounded-xl p-4 mb-4">
        <div className="flex items-end gap-2 flex-wrap">
          <label className="text-sm">
            生成数量
            <input
              type="number" min={1} max={100} value={count}
              onChange={(e) => setCount(+e.target.value)}
              className="mt-1 w-24 rounded px-2 py-1 bg-black/40 block"
            />
          </label>
          <button
            onClick={generate}
            disabled={busy || count < 1 || count > 100}
            className="bg-chip-gold text-black px-4 py-2 rounded-full font-semibold text-sm disabled:opacity-40"
          >
            {busy ? "生成中…" : "批量生成"}
          </button>
          <span className="text-xs opacity-70 ml-2">共 {codes.length} 个，未使用 {unused} 个</span>
        </div>

        {recent.length > 0 && (
          <div className="mt-3 p-2 bg-black/40 rounded">
            <div className="text-xs opacity-70 mb-1">本次生成（复制发给朋友）：</div>
            <div className="font-mono text-sm break-all select-all">{recent.join("  ")}</div>
          </div>
        )}
      </div>

      <div className="bg-feltLight rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-black/40 text-white/70">
            <tr>
              <th className="text-left px-3 py-2">邀请码</th>
              <th className="text-left px-3 py-2">创建时间</th>
              <th className="text-left px-3 py-2">使用者</th>
              <th className="text-left px-3 py-2">使用时间</th>
            </tr>
          </thead>
          <tbody>
            {codes.map((c) => (
              <tr key={c.id} className="border-t border-white/5">
                <td className="px-3 py-2 font-mono">{c.code}</td>
                <td className="px-3 py-2 text-white/60">
                  {c.created_at ? new Date(c.created_at).toLocaleString() : "-"}
                </td>
                <td className="px-3 py-2">
                  {c.used_by ? <span className="text-red-300">user#{c.used_by}</span> : <span className="text-green-300">未使用</span>}
                </td>
                <td className="px-3 py-2 text-white/60">
                  {c.used_at ? new Date(c.used_at).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
            {codes.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-6 text-center text-white/60">暂无邀请码</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
