import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { Keys, Orgs } from "./pages";
import * as session from "./session";

function withSession(api: Partial<Api>, orgs = [{ id: "o1", name: "Acme", slug: "acme", role: "owner" }]) {
  vi.spyOn(session, "useSession").mockReturnValue({
    cfg: {} as never, api: api as Api, reload: async () => {}, signOut: async () => {},
    me: { sub: "s", email: "a@b.c", name: null, organizations: orgs },
  });
}

describe("pages", () => {
  it("lists organizations with links", () => {
    withSession({});
    render(<MemoryRouter><Orgs /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Acme" }).getAttribute("href")).toBe("/orgs/o1");
  });

  it("hides revoked API keys", async () => {
    withSession({
      apiKeys: async () => [
        { id: "1", name: "live", prefix: "tsp_ab", scopes: [], last_used_at: null, revoked_at: null },
        { id: "2", name: "old", prefix: "tsp_cd", scopes: [], last_used_at: null, revoked_at: "2026-01-01" },
      ],
    });
    render(<MemoryRouter initialEntries={["/orgs/o1/keys"]}><Keys /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    expect(screen.queryByText("old")).toBeNull();
  });
});
