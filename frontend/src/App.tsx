import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import Login from "./routes/Login";
import Lobby from "./routes/Lobby";
import Table from "./routes/Table";
import Hands from "./routes/Hands";
import Admin from "./routes/Admin";
import { API } from "./lib/api";
import { useAuth } from "./store/auth";

function Guard({ children }: { children: JSX.Element }) {
  const token = useAuth((s) => s.token);
  const user = useAuth((s) => s.user);
  const hydrate = useAuth((s) => s.hydrate);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();

  useEffect(() => {
    if (token && !user) {
      API.me().then(hydrate).catch(() => { logout(); navigate("/login"); });
    }
  }, [token, user, hydrate, logout, navigate]);

  if (!token) return <Navigate to="/login" replace />;
  if (!user) return <div className="flex items-center justify-center h-full">加载中…</div>;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guard><Lobby /></Guard>} />
      <Route path="/hands" element={<Guard><Hands /></Guard>} />
      <Route path="/room/:code" element={<Guard><Table /></Guard>} />
      <Route path="/admin" element={<Guard><Admin /></Guard>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
