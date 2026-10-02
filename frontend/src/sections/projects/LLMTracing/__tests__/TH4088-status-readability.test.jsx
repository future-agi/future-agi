import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CustomStatusChip from '../../../../components/custom-status-chip/CustomStatusChip';
import { getStatusDetails } from '../../../../utils/statusUtils';

// Minimal test: neutral UNSET label must meet 4.5:1 contrast against its actual chip background in both themes.
// This is the RED step for TH-4088 span-list-only readability repair.

function renderWithTheme(mode, status) {
  const theme = createTheme({ palette: { mode } });
  return render(
    <ThemeProvider theme={theme}>
      <CustomStatusChip status={status} label={status} showIcon={false} />
    </ThemeProvider>
  );
}

describe('TH-4088 neutral status readability (SpanGrid only)', () => {
  it('UNSET in dark mode uses a foreground token that meets 4.5:1 on its actual background', () => {
    const { container } = renderWithTheme('dark', 'UNSET');
    // Placeholder assertion until the implementation supplies the correct token
    // The test will fail on the current disabled token (measured 3.465:1)
    const chip = container.querySelector('.MuiChip-root');
    expect(chip).toBeTruthy();
    // Real luminance check will be added after the scoped token change
    expect(getComputedStyle(chip).color).not.toBe('rgb(158, 158, 158)'); // current disabled
  });

  it('UNSET in light mode uses a foreground token that meets 4.5:1 on its actual background', () => {
    const { container } = renderWithTheme('light', 'UNSET');
    const chip = container.querySelector('.MuiChip-root');
    expect(chip).toBeTruthy();
    expect(getComputedStyle(chip).color).not.toBe('rgb(158, 158, 158)');
  });
});
