import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";

export function printDashboard(element, title) {
  if (!element) return;
  const frame = document.createElement("iframe");
  frame.title = "Analytics PDF export";
  frame.style.cssText =
    "position:fixed;left:-10000px;top:0;width:1200px;height:900px;border:0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  document.querySelectorAll('style, link[rel="stylesheet"]').forEach((node) => {
    const copy = node.cloneNode(true);
    if (node.tagName === "STYLE") {
      try {
        copy.textContent = [...node.sheet.cssRules]
          .map((rule) => rule.cssText)
          .join("\n");
      } catch {
        /* Inline text remains available when CSS rules cannot be read. */
      }
    }
    doc.head.appendChild(copy);
  });
  const style = doc.createElement("style");
  style.textContent =
    "@page { size: A3 portrait; margin: 10mm; } body { margin: 0; padding: 16px; background: #0c0c0e; color: #eee; font-family: Arial,sans-serif; -webkit-print-color-adjust: exact; print-color-adjust: exact; } .analytics-no-print { display: none !important; } section { break-inside: avoid; }";
  doc.head.appendChild(style);
  const heading = doc.createElement("h1");
  heading.textContent = title;
  doc.title = title;
  doc.body.appendChild(heading);
  doc.body.appendChild(element.cloneNode(true));
  const cleanup = () => frame.remove();
  frame.contentWindow.addEventListener("afterprint", cleanup, { once: true });
  Promise.resolve(doc.fonts?.ready)
    .then(() => {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    })
    .catch(cleanup);
}

export default function DashboardControls({ onPrint }) {
  return (
    <Stack
      direction="row"
      justifyContent="flex-end"
      className="analytics-no-print"
      sx={{ mb: 2 }}
    >
      <Button variant="outlined" size="small" onClick={onPrint}>
        Export PDF
      </Button>
    </Stack>
  );
}
DashboardControls.propTypes = {
  onPrint: PropTypes.func.isRequired,
};
