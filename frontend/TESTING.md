# Frontend Testing

> This doc covers **frontend-specific** Vitest + React Testing Library conventions.
> For the full pipeline (git hooks, CI, backend testing, coverage), see [`TESTING.md`](../TESTING.md) at repo root.

## Overview

The frontend uses **Vitest** as the testing framework along with **React Testing Library** for component testing. The setup is optimized for testing React components in a Vite-based application.

## Testing Stack

- **Vitest**: Fast unit testing framework optimized for Vite projects
- **React Testing Library**: Testing utilities focused on testing behavior over implementation
- **Jest DOM**: Additional matchers for better assertions
- **User Event**: Library for simulating user interactions
- **jsdom**: DOM environment for Node.js testing

## Quick Start

### Running Tests

```bash
# Run tests in watch mode (recommended for development)
yarn test

# Run tests once
yarn test:run

# Run tests with UI (browser-based test runner)
yarn test:ui

# Run tests in watch mode
yarn test:watch

# Run tests with coverage report
yarn test:coverage

# Run the dedicated unit test directory
yarn test:unit

# Run the dedicated integration test directory
yarn test:integration
```

### Test File Structure

Tests should be placed in one of these locations:
- `src/components/ComponentName/ComponentName.test.jsx` - Next to the component
- `src/__tests__/ComponentName.test.jsx` - In a dedicated tests directory
- `src/components/__tests__/ComponentName.test.jsx` - In component-specific test directories
- `src/__tests__/unit/` - Included by `yarn test:unit`
- `src/__tests__/integration/` - Included by `yarn test:integration`

`test:unit` and `test:integration` are directory-scoped. Test titles no
longer classify files for those commands; use `yarn test:run -t <pattern>` for
ad hoc title filtering. Component-local and feature-local tests outside the
dedicated directories remain covered by `yarn test:run`, `yarn test:coverage`,
and the full-suite CI jobs.

## Configuration

### Vitest Configuration (`vite.config.js`)

```javascript
test: {
  globals: true,
  environment: 'jsdom',
  setupFiles: ['./src/setupTests.js'],
  css: true,
}
```

### Setup File (`src/setupTests.js`)

Contains global test configuration including:
- Jest DOM matchers
- Global mocks for browser APIs (IntersectionObserver, ResizeObserver, etc.)
- Window.matchMedia mock

### Test Utilities (`src/utils/test-utils.jsx`)

Custom render function that includes:
- Router provider
- Material-UI theme provider
- Other necessary context providers

## Writing Tests

### Basic Component Test

```javascript
import { describe, it, expect } from 'vitest';
import { render, screen } from '../../utils/test-utils';
import MyComponent from './MyComponent';

describe('MyComponent', () => {
  it('renders correctly', () => {
    render(<MyComponent />);
    expect(screen.getByText('Hello World')).toBeInTheDocument();
  });
});
```

### Testing User Interactions

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, userEvent } from '../../utils/test-utils';
import Button from './Button';

describe('Button', () => {
  it('calls onClick when clicked', async () => {
    const handleClick = vi.fn();
    const user = userEvent.setup();
    
    render(<Button onClick={handleClick}>Click me</Button>);
    
    await user.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });
});
```

### Testing Async Operations

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '../../utils/test-utils';
import UserProfile from './UserProfile';

// Mock fetch globally
globalThis.fetch = vi.fn();

describe('UserProfile', () => {
  beforeEach(() => {
    fetch.mockClear();
  });

  it('displays user data after loading', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ name: 'John Doe', email: 'john@example.com' }),
    });

    render(<UserProfile userId={1} />);

    await waitFor(() => {
      expect(screen.getByText('John Doe')).toBeInTheDocument();
    });
  });
});
```

## Testing Patterns

### 1. Arrange, Act, Assert

```javascript
it('increments counter when button is clicked', async () => {
  // Arrange
  const user = userEvent.setup();
  render(<Counter initialValue={0} />);
  
  // Act
  await user.click(screen.getByText('Increment'));
  
  // Assert
  expect(screen.getByText('Count: 1')).toBeInTheDocument();
});
```

### 2. Testing Different States

```javascript
describe('LoadingButton', () => {
  it('shows loading text when loading', () => {
    render(<LoadingButton isLoading>Submit</LoadingButton>);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('shows button text when not loading', () => {
    render(<LoadingButton isLoading={false}>Submit</LoadingButton>);
    expect(screen.getByText('Submit')).toBeInTheDocument();
  });
});
```

### 3. Mock Functions and Modules

```javascript
// Mock a specific module
vi.mock('./api', () => ({
  fetchUsers: vi.fn(),
}));

// Mock a function
const mockFunction = vi.fn();
mockFunction.mockReturnValue('mocked value');
mockFunction.mockResolvedValue(Promise.resolve('async value'));
```

## Best Practices

### 1. Test Behavior, Not Implementation

❌ **Don't test internal state:**
```javascript
// Bad
expect(wrapper.state('count')).toBe(5);
```

✅ **Test user-visible behavior:**
```javascript
// Good
expect(screen.getByText('Count: 5')).toBeInTheDocument();
```

### 2. Use Descriptive Test Names

```javascript
// Good test names
it('displays error message when email validation fails')
it('submits form with correct data when all fields are filled')
it('disables submit button when form is invalid')
```

### 3. Query Priority (in order of preference)

1. `getByRole` - Most accessible
2. `getByLabelText` - Form elements
3. `getByPlaceholderText` - Inputs
4. `getByText` - Non-interactive elements
5. `getByTestId` - Last resort

### 4. Clean Up After Tests

```javascript
describe('Component with side effects', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });
});
```

### 5. Test Accessibility

```javascript
it('has proper ARIA labels', () => {
  render(<SearchInput />);
  expect(screen.getByRole('searchbox')).toHaveAccessibleName('Search users');
});
```

## Example Test Files

The project includes several example test files:

1. **`src/components/loading-screen/loading-screen.test.jsx`**
   - Basic component rendering
   - Props testing
   - Snapshot testing

2. **`src/components/IncrementerButton/IncrementerButton.test.jsx`**
   - User interactions
   - Event handling
   - Disabled states
   - Edge cases

3. **`src/components/__tests__/examples/UserProfile.test.jsx`**
   - Async operations
   - API mocking
   - Loading states
   - Error handling

## Common Testing Scenarios

### Forms

```javascript
it('submits form with user input', async () => {
  const mockSubmit = vi.fn();
  const user = userEvent.setup();
  
  render(<ContactForm onSubmit={mockSubmit} />);
  
  await user.type(screen.getByLabelText(/name/i), 'John Doe');
  await user.type(screen.getByLabelText(/email/i), 'john@example.com');
  await user.click(screen.getByRole('button', { name: /submit/i }));
  
  expect(mockSubmit).toHaveBeenCalledWith({
    name: 'John Doe',
    email: 'john@example.com',
  });
});
```

### Conditional Rendering

```javascript
it('shows success message after successful submission', async () => {
  render(<Form />);
  
  // ... fill form and submit
  
  await waitFor(() => {
    expect(screen.getByText('Form submitted successfully!')).toBeInTheDocument();
  });
});
```

### Error Boundaries

```javascript
it('catches and displays errors', () => {
  const ThrowError = () => {
    throw new Error('Test error');
  };
  
  render(
    <ErrorBoundary>
      <ThrowError />
    </ErrorBoundary>
  );
  
  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
});
```

## Debugging Tests

### 1. Use `screen.debug()`

```javascript
it('debug test', () => {
  render(<MyComponent />);
  screen.debug(); // Prints the DOM tree
});
```

### 2. Use `logRoles()`

```javascript
import { logRoles } from '@testing-library/react';

it('find available roles', () => {
  const { container } = render(<MyComponent />);
  logRoles(container); // Shows all available roles
});
```

### 3. Use Test UI

```bash
yarn test:ui
```

Opens a browser-based interface for running and debugging tests.

## CI/CD Integration

Add to your CI pipeline:

```yaml
- name: Run tests
  run: yarn test:run

- name: Generate coverage
  run: yarn test:coverage
```

## Troubleshooting

### Common Issues

1. **Tests timeout on async operations**
   - Use `waitFor()` for async state changes
   - Increase timeout if needed: `waitFor(() => {}, { timeout: 5000 })`

2. **Element not found errors**
   - Use `screen.debug()` to see the actual DOM
   - Check if you're using the right query method
   - Ensure the element is actually rendered

3. **Mocks not working**
   - Clear mocks between tests with `vi.clearAllMocks()`
   - Mock modules at the top level of the test file
   - Use `vi.mocked()` for better TypeScript support

4. **Theme/Provider errors**
   - Use the custom render function from `test-utils.jsx`
   - Ensure all required providers are included in the wrapper

## Resources

- [Vitest Documentation](https://vitest.dev/)
- [React Testing Library](https://testing-library.com/docs/react-testing-library/intro/)
- [Jest DOM Matchers](https://github.com/testing-library/jest-dom)
- [User Event Documentation](https://testing-library.com/docs/user-event/intro)
- [Testing Best Practices](https://kentcdodds.com/blog/common-mistakes-with-react-testing-library) 