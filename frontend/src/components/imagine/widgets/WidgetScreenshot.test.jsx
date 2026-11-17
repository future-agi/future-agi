import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WidgetScreenshot from "./WidgetScreenshot";

let notifyResize;

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        notifyResize = callback;
      }

      observe() {}

      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const config = {
  screenshotUrl: "/screenshot-wide.png",
  imageWidth: 1920,
  imageHeight: 1080,
  clicks: [
    { x: 0, y: 0 },
    { x: 960, y: 540 },
    { x: 1920, y: 1080 },
  ],
};

function resize(width, height) {
  act(() => notifyResize([{ contentRect: { width, height } }]));
}

function loadImage(container, width, height) {
  const image = container.querySelector("img");
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: width },
    naturalHeight: { configurable: true, value: height },
  });
  fireEvent.load(image);
}

function expectOverlay(container, points) {
  const markers = container.querySelectorAll('circle[r="10"]');
  const rings = container.querySelectorAll('circle[r="16"]');
  const lines = container.querySelectorAll("line");
  expect(markers).toHaveLength(points.length);
  expect(rings).toHaveLength(points.length);
  expect(lines).toHaveLength(points.length - 1);
  points.forEach(([x, y], i) => {
    [markers[i], rings[i]].forEach((marker) => {
      expect(Number(marker.getAttribute("cx"))).toBeCloseTo(x);
      expect(Number(marker.getAttribute("cy"))).toBeCloseTo(y);
    });
    if (i > 0) {
      const [prevX, prevY] = points[i - 1];
      expect(Number(lines[i - 1].getAttribute("x1"))).toBeCloseTo(prevX);
      expect(Number(lines[i - 1].getAttribute("y1"))).toBeCloseTo(prevY);
      expect(Number(lines[i - 1].getAttribute("x2"))).toBeCloseTo(x);
      expect(Number(lines[i - 1].getAttribute("y2"))).toBeCloseTo(y);
    }
  });
}

const letterboxedPoints = [
  [0, 31.25],
  [300, 200],
  [600, 368.75],
];
const pillarboxedPoints = [
  [160, 0],
  [400, 135],
  [640, 270],
];

describe("WidgetScreenshot contained-image overlays (W10-DASHBOARDS-L08)", () => {
  it.each([
    ["letterboxing", 600, 400, letterboxedPoints],
    ["pillarboxing", 800, 270, pillarboxedPoints],
    [
      "matching aspect ratios",
      640,
      360,
      [
        [0, 0],
        [320, 180],
        [640, 360],
      ],
    ],
    [
      "upscaling",
      3840,
      2400,
      [
        [0, 120],
        [1920, 1200],
        [3840, 2280],
      ],
    ],
  ])(
    "aligns markers and every line endpoint with %s",
    (_name, width, height, points) => {
      const { container } = render(<WidgetScreenshot config={config} />);
      resize(width, height);
      expectOverlay(container, points);
    },
  );

  it("repositions markers and lines together after a container resize", () => {
    const { container } = render(<WidgetScreenshot config={config} />);
    resize(600, 400);
    expectOverlay(container, letterboxedPoints);
    resize(800, 270);
    expectOverlay(container, pillarboxedPoints);
  });

  it.each([
    ["missing", undefined, undefined],
    ["stale", 1000, 1000],
  ])(
    "uses loaded image dimensions when configured dimensions are %s",
    (_name, imageWidth, imageHeight) => {
      const { container } = render(
        <WidgetScreenshot config={{ ...config, imageWidth, imageHeight }} />,
      );
      resize(600, 400);
      loadImage(container, 1920, 1080);
      expectOverlay(container, letterboxedPoints);
    },
  );

  it("does not reuse loaded dimensions for a different screenshot URL", () => {
    const { container, rerender } = render(
      <WidgetScreenshot config={config} />,
    );
    resize(600, 400);
    loadImage(container, 1920, 1080);
    expectOverlay(container, letterboxedPoints);

    rerender(
      <WidgetScreenshot
        config={{
          screenshotUrl: "/screenshot-portrait.png",
          imageWidth: 1080,
          imageHeight: 1920,
          clicks: [
            { x: 0, y: 0 },
            { x: 540, y: 960 },
            { x: 1080, y: 1920 },
          ],
        }}
      />,
    );
    const portraitPoints = [
      [187.5, 0],
      [300, 200],
      [412.5, 400],
    ];
    expectOverlay(container, portraitPoints);
    loadImage(container, 1080, 1920);
    expectOverlay(container, portraitPoints);
  });

  it.each([
    ["loading", config.screenshotUrl],
    ["absent", undefined],
  ])(
    "keeps raw coordinates without dimensions when the image is %s",
    (_name, screenshotUrl) => {
      const { container } = render(
        <WidgetScreenshot
          config={{
            ...config,
            screenshotUrl,
            imageWidth: undefined,
            imageHeight: undefined,
          }}
        />,
      );
      resize(600, 400);
      expectOverlay(container, [
        [0, 0],
        [960, 540],
        [1920, 1080],
      ]);
    },
  );
});
