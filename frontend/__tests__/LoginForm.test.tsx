import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/LoginForm";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

const loginMock = vi.fn();
vi.mock("@/lib/api/auth", () => ({
  login: (...args: unknown[]) => loginMock(...args),
}));

const setStoredTokenMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  setStoredToken: (...args: unknown[]) => setStoredTokenMock(...args),
  getStoredToken: () => null,
  clearStoredToken: vi.fn(),
}));

describe("LoginForm", () => {
  beforeEach(() => {
    pushMock.mockReset();
    loginMock.mockReset();
    setStoredTokenMock.mockReset();
  });

  it("renders email and password fields", () => {
    render(<LoginForm />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("logs in and redirects to the dashboard on success", async () => {
    loginMock.mockResolvedValue({
      access_token: "token-123",
      token_type: "bearer",
      user: { id: "1", email: "a@b.com", first_name: "A", last_name: "B" },
    });

    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "a@b.com");
    await user.type(screen.getByLabelText("Password"), "supersecret");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(setStoredTokenMock).toHaveBeenCalledWith("token-123");
  });

  it("shows an error message when login fails", async () => {
    const { ApiError } = await import("@/lib/http");
    loginMock.mockRejectedValue(new ApiError(401, "UNAUTHORIZED", "Invalid email or password.", {}));

    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "a@b.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(pushMock).not.toHaveBeenCalled();
  });
});
