import { createClient } from "@supabase/supabase-js";

export const authRequired = import.meta.env.VITE_AUTH_MODE === "supabase";
// Email callbacks always return to the deployed root, never the current hash
// route or a local preview. Supabase owns the callback fragment until consumed.
export const AUTH_REDIRECT_URL = "https://cninfo-analyzer-web.pages.dev/";
const url = import.meta.env.VITE_SUPABASE_URL?.trim();
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();

// Only a publishable key belongs in this browser bundle. Never use service_role.
export const supabase = url && key?.startsWith("sb_publishable_")
  ? createClient(url, key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } })
  : null;

export async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit, requireUser = true): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (supabase) {
    const { data, error } = await supabase.auth.getSession();
    if (error) throw error;
    if (data.session?.access_token) headers.set("Authorization", `Bearer ${data.session.access_token}`);
    else if (authRequired && requireUser) throw new Error("请先登录，再提交或查看自己的任务。");
  } else if (authRequired && requireUser) {
    throw new Error("登录服务尚未配置，暂时无法操作任务。");
  }
  return fetch(input, { ...init, headers });
}
