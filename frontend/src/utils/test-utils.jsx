import { render } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import PropTypes from "prop-types";
import { palette } from "src/theme/palette";


const testTheme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

/**
 * Custom render function that includes common providers
 * This makes testing components that rely on context much easier
 */
const customRender = (ui, options = {}) => {
  const { theme: customTheme = testTheme, ...renderOptions } = options;

  const Wrapper = ({ children }) => (
    <BrowserRouter>
      <ThemeProvider theme={customTheme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </BrowserRouter>
  );

  Wrapper.propTypes = {
    children: PropTypes.node.isRequired,
  };

  return render(ui, { wrapper: Wrapper, ...renderOptions });
};

/**
 * Custom render function for components that need Router with specific routes
 */
const renderWithRouter = (ui, { route = "/", ...options } = {}) => {
  window.history.pushState({}, "Test page", route);
  return customRender(ui, options);
};

/**
 * Mock data factories for common test scenarios
 */
export const mockUser = {
  id: 1,
  name: "John Doe",
  email: "john@example.com",
  avatar: "https://example.com/avatar.jpg",
};

export const mockUsers = [
  mockUser,
  {
    id: 2,
    name: "Jane Smith",
    email: "jane@example.com",
    avatar: "https://example.com/avatar2.jpg",
  },
];

// Export everything from testing library
// eslint-disable-next-line
export * from "@testing-library/react";
export { userEvent } from "@testing-library/user-event";

// Override render method
export { customRender as render, renderWithRouter };
