import { expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, renderWithRouter, screen, within } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { useSkillLabels } from "src/api/skills";
import VersionsTab from "./VersionsTab";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
vi.mock("src/api/skills", () => ({
  useSkillLabels: vi.fn(),
  useSkillVersions: () => ({ data: { results: [{ id: "v1", number: 1, note: "Initial version", labels: [], createdAt: "2026-11-01T12:00:00Z", fileCount: 1, digest: "sha256:abc" }], count: 1 } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./MoveLabelDialog", () => ({ default: () => null }));
vi.mock("./RestoreVersionDialog", () => ({ default: () => null }));

it("opens the retained removal event after the last current label disappears", async () => {
  const skill = { id: "skill-1", name: "release-review", latestVersion: 1 };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = () => <QueryClientProvider client={client}><VersionsTab skill={skill} /></QueryClientProvider>;
  useSkillLabels.mockReturnValue({ data: [{ name: "production", version: 1 }] });
  const rendered = renderWithRouter(view());
  expect(screen.getByRole("button", { name: "History for production" })).toBeInTheDocument();
  useSkillLabels.mockReturnValue({ data: [] });
  axios.get.mockResolvedValue({ data: { result: { results: [{ label: "production", from_version: 1, to_version: null, actor: { id: "reviewer", name: "Avery", kind: "user" }, note: "Removed after review", created_at: "2026-11-03T12:00:00Z" }], count: 1, page: 1, page_size: 25, total_pages: 1 } } });
  rendered.rerender(view());
  fireEvent.click(screen.getByRole("button", { name: "Label history" }));
  const drawer = within(screen.getByRole("dialog", { name: "Label history" }));
  expect(await drawer.findByText("production: v1 → Removed")).toBeInTheDocument();
  expect(drawer.getByText("Removed after review")).toBeInTheDocument();
  expect(drawer.getByText(/Avery/)).toBeInTheDocument();
  expect(axios.get.mock.lastCall[1].params).toEqual({ label: undefined, page: 1, page_size: 25 });
});
