/* eslint-disable no-console */
import * as Sentry from "@sentry/react";

import { CURRENT_ENVIRONMENT } from "../config-global";

/**
 * Production-ready logger utility with Sentry integration
 * Automatically handles different log levels based on environment
 */
class Logger {
  constructor() {
    this.isDevelopment =
      CURRENT_ENVIRONMENT === "local" || CURRENT_ENVIRONMENT === "dev";
    this.isProduction = CURRENT_ENVIRONMENT === "production";
    this.shouldSendToSentry = CURRENT_ENVIRONMENT !== "local"; // Send to Sentry in dev and production only
  }

  /**
   * Helper method to ensure message is a string
   * @private
   * @param {any} message - The message to validate
   * @param {any} data - Additional data
   * @param {string} methodName - Name of the logger method calling this
   * @returns {{message: string, data: any}} - Normalized message and data
   */
  _ensureStringMessage(message, data, methodName) {
    if (typeof message !== "string") {
      // Only warn in development
      if (this.isDevelopment) {
        console.warn(
          `⚠️  logger.${methodName}() called incorrectly! First parameter must be a string.`,
          `\n   Expected: logger.${methodName}('descriptive message', data)`,
          "\n   Received:",
          message,
        );
      }

      // Extract a meaningful string message
      const extractedMessage =
        message?.message ||
        message?.error ||
        message?.detail ||
        message?.statusText ||
        `Unknown ${methodName}`;

      // Move original object to data parameter
      return {
        message: extractedMessage,
        data: data || message,
      };
    }

    return { message, data };
  }

  /**
   * Debug level logging - only in development
   * @param {string} message - The log message
   * @param {any} data - Additional data to log
   * @param {object} context - Additional context for Sentry
   */
  debug(message, data = null, context = {}) {
    if (this.isDevelopment) {
      console.log("🐛 DEBUG:", message, data || "");
    }

    // Send debug info to Sentry only in development for troubleshooting
    if (this.isDevelopment && this.shouldSendToSentry) {
      Sentry.addBreadcrumb({
        message: `DEBUG: ${message}`,
        level: "debug",
        data: data || undefined,
        ...context,
      });
    }
  }

  /**
   * Info level logging
   * @param {string} message - The log message
   * @param {any} data - Additional data to log
   * @param {object} context - Additional context for Sentry
   */
  info(message, data = null, context = {}) {
    if (this.isDevelopment) {
      console.info("ℹ️ INFO:", message, data || "");
    }

    // Add breadcrumb to Sentry for info messages
    if (this.shouldSendToSentry) {
      Sentry.addBreadcrumb({
        message: `INFO: ${message}`,
        level: "info",
        data: data || undefined,
        ...context,
      });
    }
  }

  /**
   * Warning level logging
   * @param {string} message - The warning message
   * @param {any} data - Additional data to log
   * @param {object} context - Additional context for Sentry
   */
  warn(message, data = null, context = {}) {
    // Ensure message is a string
    ({ message, data } = this._ensureStringMessage(message, data, "warn"));

    console.warn("⚠️ WARN:", message, data || "");

    // Warnings are recorded as breadcrumbs (context for a later error), NOT as
    // standalone Sentry issues. Capturing every warning as an event floods the
    // issue stream with non-actionable noise.
    if (this.shouldSendToSentry) {
      Sentry.addBreadcrumb({
        category: "warning",
        level: "warning",
        message,
        data: { data: data || undefined, ...context },
      });
    }
  }

  /**
   * Error level logging with Sentry integration
   * @param {string} message - The error message
   * @param {Error|any} error - The error object or additional data
   * @param {object} context - Additional context for Sentry
   */
  error(message, error = null, context = {}) {
    // Ensure message is a string
    ({ message, data: error } = this._ensureStringMessage(
      message,
      error,
      "error",
    ));

    console.error("❌ ERROR:", message, error || "");

    // Capture error in Sentry with full context
    if (this.shouldSendToSentry) {
      if (error instanceof Error) {
        Sentry.captureException(error, {
          tags: {
            level: "error",
            source: "logger",
          },
          contexts: {
            error_info: {
              message,
              ...context,
            },
          },
        });
      } else {
        Sentry.captureMessage(message, {
          level: "error",
          tags: {
            level: "error",
            source: "logger",
          },
          extra: {
            data: error || undefined,
            ...context,
          },
        });
      }
    }
  }

  /**
   * Fatal error logging - for critical errors that require immediate attention
   * @param {string} message - The fatal error message
   * @param {Error|any} error - The error object or additional data
   * @param {object} context - Additional context for Sentry
   */
  fatal(message, error = null, context = {}) {
    // Ensure message is a string
    ({ message, data: error } = this._ensureStringMessage(
      message,
      error,
      "fatal",
    ));

    console.error("💀 FATAL:", message, error || "");

    // Capture fatal error in Sentry with high priority
    if (this.shouldSendToSentry) {
      if (error instanceof Error) {
        Sentry.captureException(error, {
          level: "fatal",
          tags: {
            level: "fatal",
            source: "logger",
            priority: "high",
          },
          contexts: {
            fatal_error: {
              message,
              timestamp: new Date().toISOString(),
              ...context,
            },
          },
        });
      } else {
        Sentry.captureMessage(message, {
          level: "fatal",
          tags: {
            level: "fatal",
            source: "logger",
            priority: "high",
          },
          extra: {
            data: error || undefined,
            timestamp: new Date().toISOString(),
            ...context,
          },
        });
      }
    }
  }

  /**
   * Set user context for Sentry
   * @param {object} user - User information
   */
  setUser(user) {
    if (this.shouldSendToSentry) {
      Sentry.setUser(user);
    }
  }

  /**
   * Set additional context for Sentry
   * @param {string} key - Context key
   * @param {object} context - Context data
   */
  setContext(key, context) {
    if (this.shouldSendToSentry) {
      Sentry.setContext(key, context);
    }
  }

  /**
   * Add tags to Sentry context
   * @param {object} tags - Tags to add
   */
  setTags(tags) {
    if (this.shouldSendToSentry) {
      Sentry.setTags(tags);
    }
  }

  /**
   * Add a breadcrumb to Sentry
   * @param {object} breadcrumb - Breadcrumb data
   */
  addBreadcrumb(breadcrumb) {
    if (this.shouldSendToSentry) {
      Sentry.addBreadcrumb(breadcrumb);
    }
  }

  // Performance logging for development
  time(label) {
    if (this.isDevelopment) {
      console.time(label);
    }
  }

  timeEnd(label) {
    if (this.isDevelopment) {
      console.timeEnd(label);
    }
  }

  /**
   * Performance monitoring with Sentry
   * @param {string} name - Transaction name
   * @param {function} callback - Function to monitor
   * @param {object} context - Additional context
   */
  async withPerformance(name, callback, context = {}) {
    this.time(name);

    try {
      // Add performance breadcrumb
      if (this.shouldSendToSentry) {
        Sentry.addBreadcrumb({
          message: `Performance monitoring started: ${name}`,
          level: "info",
          category: "performance",
          data: context,
        });
      }

      const result = await callback();

      if (this.shouldSendToSentry) {
        Sentry.addBreadcrumb({
          message: `Performance monitoring completed: ${name}`,
          level: "info",
          category: "performance",
        });
      }

      return result;
    } catch (error) {
      this.error(`Performance monitoring failed for ${name}`, error, context);
      throw error;
    } finally {
      this.timeEnd(name);
    }
  }
}

// Export singleton instance
export const logger = new Logger();

// Export default for easy importing
export default logger;
