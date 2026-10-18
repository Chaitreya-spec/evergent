import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskComposer } from "@/components/composer/TaskComposer";
import { agents, models, provider } from "../fixtures/data";
import { mockApi, renderWithClient } from "../fixtures/render";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

const project = { id: "p-1", name: "Portal", description: null, max_cost_usd: 5, max_tokens: null, created_at: "" };

beforeEach(() => push.mockReset());
afterEach(() => vi.unstubAllGlobals());

describe("TaskComposer", () => {
  it("creates a workflow with the chosen options and an idempotency key", async () => {
    const calls = mockApi({
      "/projects": { items: [project], total: 1, limit: 200, offset: 0 },
      "/models": models,
      "/providers": [provider()],
      "/agents": agents,
      "POST /workflows": { id: "wf-9" },
    });
    renderWithClient(<TaskComposer />);
    const user = userEvent.setup();

    const submit = await screen.findByRole("button", { name: /plan it/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/what should the team build/i), "Build a billing API with tests");
    await user.click(screen.getByRole("radio", { name: "Quality" }));
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    await waitFor(() => expect(push).toHaveBeenCalledWith("/workflows/wf-9"));
    const create = calls.find((c) => c.method === "POST" && c.path === "/workflows")!;
    expect(create.body).toMatchObject({
      project_id: "p-1",
      goal: "Build a billing API with tests",
      routing_strategy: "highest_quality",
      require_approval: true,
      autostart: true,
    });
    expect(create.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("refuses to start when no provider can run anything", async () => {
    mockApi({
      "/projects": { items: [project], total: 1, limit: 200, offset: 0 },
      "/models": models,
      "/providers": [provider({ configured: false })],
      "/agents": agents,
    });
    renderWithClient(<TaskComposer />);
    const user = userEvent.setup();
    expect(await screen.findByText(/no model provider is configured/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/what should the team build/i), "Build a billing API with tests");
    expect(screen.getByRole("button", { name: /plan it/i })).toBeDisabled();
  });

  it("tells a read-only viewer why it cannot start work", async () => {
    mockApi({
      "/auth/me": { id: "u", email: "v@example.com", full_name: null, role: "viewer", is_active: true, created_at: "" },
      "/projects": { items: [project], total: 1, limit: 200, offset: 0 },
      "/models": models,
      "/providers": [provider()],
      "/agents": agents,
    });
    renderWithClient(<TaskComposer />);
    const user = userEvent.setup();
    expect(await screen.findByText("This account is read-only")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/what should the team build/i), "Build a billing API with tests");
    expect(screen.getByRole("button", { name: /plan it/i })).toBeDisabled();
  });

  it("asks for a first project when there is none", async () => {
    mockApi({
      "/projects": { items: [], total: 0, limit: 200, offset: 0 },
      "/models": models,
      "/providers": [provider()],
      "/agents": agents,
    });
    renderWithClient(<TaskComposer />);
    expect(await screen.findByText(/name your first one/i)).toBeInTheDocument();
  });
});
