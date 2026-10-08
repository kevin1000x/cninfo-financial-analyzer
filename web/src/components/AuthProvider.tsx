import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "@/lib/auth";
import { AuthContext } from "@/lib/authContext";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{ session: Session | null; ready: boolean; error: string | null; recovering: boolean }>({ session: null, ready: !supabase, error: null, recovering: false });
  useEffect(() => {
    if (!supabase) return;
    let live = true;
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (live) setState(previous => ({ session, ready: true, error: null, recovering: event === "PASSWORD_RECOVERY" || (event !== "SIGNED_OUT" && previous.recovering) }));
    });
    void supabase.auth.getSession().then(({ data, error }) => {
      if (live) setState(previous => ({ ...previous, session: data.session, ready: true, error: error?.message ?? null }));
    }).catch(() => {
      if (live) setState({ session: null, ready: true, error: "暂时无法恢复登录，请重新登录。", recovering: false });
    });
    return () => { live = false; subscription.unsubscribe(); };
  }, []);
  return <AuthContext.Provider value={{ ...state, finishRecovery: () => setState(previous => ({ ...previous, recovering: false })) }}>{children}</AuthContext.Provider>;
}
