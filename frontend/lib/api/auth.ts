import { api } from "@/lib/http";
import type { AuthResponse } from "@/types/user";

export interface RegisterInput {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export function register(input: RegisterInput): Promise<AuthResponse> {
  return api.post<AuthResponse>("/auth/register", input, { auth: false });
}

export function login(input: LoginInput): Promise<AuthResponse> {
  return api.post<AuthResponse>("/auth/login", input, { auth: false });
}

export function logout(): Promise<void> {
  return api.post<void>("/auth/logout");
}
