import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, MenuItem, Select, Typography } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";

const isPending = (job) => job?.status === "queued" || job?.status === "running";

export default function BulkExportButton({ projectId, rowType, filters, attributeKeys = [] }) {
  const [format, setFormat] = useState("csv");
  const [job, setJob] = useState(null);
  const [creating, setCreating] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  const creatingRef = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    // Creation never returns a download URL, even if a fast worker has
    // already finished. Obtain it only from the scoped status endpoint.
    if (!job?.id || (!isPending(job) && job.status !== "completed")) return undefined;
    let cancelled = false;
    let timer;
    const poll = async () => {
      try {
        const { data } = await axios.get(
          endpoints.project.bulkExportStatus(job.id),
        );
        if (cancelled) return;
        setJob(data);
        setError("");
        if (isPending(data)) timer = setTimeout(poll, 1500);
      } catch {
        if (!cancelled) setError("Could not check export status.");
      }
    };
    timer = setTimeout(poll, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // A poll updates status without restarting this job's polling loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, pollAttempt]);

  const createExport = async () => {
    if (creatingRef.current || downloading || isPending(job)) return;
    creatingRef.current = true;
    setCreating(true);
    setError("");
    setJob(null);
    try {
      const { data } = await axios.post(endpoints.project.createBulkExport, {
        project_id: projectId,
        row_type: rowType,
        filters: {
          filters: filters || [],
          ...(rowType === "traces" ? { attribute_keys: attributeKeys } : {}),
        },
        format,
      });
      if (mounted.current) setJob(data);
    } catch {
      if (mounted.current) setError("Export could not be queued. Please retry.");
    } finally {
      creatingRef.current = false;
      if (mounted.current) setCreating(false);
    }
  };

  const downloadExport = async () => {
    if (downloading || creatingRef.current) return;
    setDownloading(true);
    setError("");
    try {
      // Reauthorize and re-sign on every click; a completed job's cached URL
      // can expire while the page stays open after polling has stopped.
      const { data } = await axios.get(
        endpoints.project.bulkExportStatus(job.id),
      );
      if (!mounted.current) return;
      if (data.status !== "completed" || !data.download_url) throw new Error("Download unavailable");
      setJob(data);
      const link = document.createElement("a");
      link.href = data.download_url;
      link.download = "";
      link.rel = "noopener noreferrer";
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch {
      if (mounted.current) setError("Could not download export. Please retry.");
    } finally {
      if (mounted.current) setDownloading(false);
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
      <Select
        size="small"
        value={format}
        onChange={(event) => setFormat(event.target.value)}
        inputProps={{ "aria-label": "Export format" }}
        disabled={creating || isPending(job)}
      >
        <MenuItem value="csv">CSV</MenuItem>
        <MenuItem value="parquet">Parquet</MenuItem>
      </Select>
      <Button size="small" onClick={createExport} disabled={!projectId || creating || downloading || isPending(job)}>
        Export all rows
      </Button>
      {(creating || isPending(job)) && <Typography variant="caption" role="status">Preparing export{job?.row_count ? ` (${job.row_count} rows)` : ""}…</Typography>}
      {job?.status === "completed" && job.download_url && (
        <Button size="small" onClick={downloadExport} disabled={creating || downloading}>
          Download export
        </Button>
      )}
      {(error || job?.error || job?.status === "failed") && (
        <Typography variant="caption" role="alert">
          {error || job?.error || "Export could not be completed. Please retry."}
        </Typography>
      )}
      {error && isPending(job) && (
        <Button size="small" onClick={() => { setError(""); setPollAttempt((value) => value + 1); }}>
          Retry export status
        </Button>
      )}
    </Box>
  );
}

BulkExportButton.propTypes = {
  projectId: PropTypes.string,
  rowType: PropTypes.oneOf(["traces", "spans", "sessions"]).isRequired,
  filters: PropTypes.array,
  attributeKeys: PropTypes.arrayOf(PropTypes.string),
};
