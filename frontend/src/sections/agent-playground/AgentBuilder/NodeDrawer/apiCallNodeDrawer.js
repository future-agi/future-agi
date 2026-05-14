import React, { useState } from 'react';
import { Box, Button, Typography, Chip, Stack, Alert } from '@mui/material';

const METHOD_COLORS = {
  GET: '#4CAF50',
  POST: '#2196F3',
  PUT: '#FF9800',
  PATCH: '#9C27B0',
  DELETE: '#F44336',
};

const truncateUrl = (url, maxLength = 40) => {
  if (!url || url.length <= maxLength) return url;
  const protocolEnd = url.indexOf('://') + 3;
  const pathStart = url.indexOf('/', protocolEnd);
  const domain = url.substring(protocolEnd, pathStart !== -1 ? pathStart : undefined);
  const path = pathStart !== -1 ? url.substring(pathStart) : '';
  if (domain.length <= maxLength - path.length - 3) return url;
  return `${url.substring(0, protocolEnd + Math.floor((maxLength - path.length - 3) / 2))}...${path}`;
};

export const ApiCallNodeDrawer = ({ config, onChange }) => {
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const handleTestRequest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const response = await fetch('/api/agent-playground/test-http-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await response.json();
      setTestResult(data);
    } catch (_err) {
      setTestResult({ success: false, error: 'Could not load data. Please refresh.' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Stack spacing={2}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Chip
            label={config.method || 'GET'}
            sx={{
              backgroundColor: METHOD_COLORS[config.method] || '#757575',
              color: '#fff',
              fontWeight: 'bold',
            }}
          />
          <Typography variant="body2" noWrap title={config.url}>
            {truncateUrl(config.url)}
          </Typography>
        </Box>

        <Button
          variant="contained"
          onClick={handleTestRequest}
          disabled={testing || !config.url}
          fullWidth
        >
          {testing ? 'Testing...' : 'Test Request'}
        </Button>

        {testResult && (
          <Alert severity={testResult.success ? 'success' : 'error'}>
            Status: {testResult.status}
            {testResult.error && <Typography variant="caption" display="block">{testResult.error}</Typography>}
          </Alert>
        )}
      </Stack>
    </Box>
  );
};
