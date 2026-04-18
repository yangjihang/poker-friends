import { create } from "zustand";
import { AuthUser, getToken, setToken } from "../lib/api";

type AuthState = {
  user: AuthUser | null;
  token: string | null;
  setAuth: (token: string, user: AuthUser) => void;
  logout: () => void;
  hydrate: (user: AuthUser) => void;
};

export const useAuth = create<AuthState>((set) => ({
  user: null,
  token: getToken(),
  setAuth: (token, user) => {
    setToken(token);
    set({ token, user });
  },
  hydrate: (user) => set({ user }),
  logout: () => {
    setToken(null);
    set({ token: null, user: null });
  },
}));
