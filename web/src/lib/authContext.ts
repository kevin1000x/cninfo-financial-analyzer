import { createContext, useContext } from "react";
import type { Session } from "@supabase/supabase-js";

export const AuthContext = createContext<{
  session: Session | null; ready: boolean; error: string | null; recovering: boolean; finishRecovery(): void;
}>({ session: null, ready: false, error: null, recovering: false, finishRecovery() {} });
export const useAuth = () => useContext(AuthContext);
