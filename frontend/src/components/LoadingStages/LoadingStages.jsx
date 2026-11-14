import React, { useState, useEffect, useRef } from "react";
import "./LoadingStages.css";
import PropTypes from "prop-types";
import SvgColor from "../svg-color/svg-color";

const LoadingStages = ({
  steps = [],
  initialStep = 0,
  circularFromStart = false,
  style = {},
}) => {
  const safeInitialStep =
    steps.length > 0 ? Math.min(Math.max(initialStep, 0), steps.length - 1) : 0;

  const [activeStep, setActiveStep] = useState(safeInitialStep);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [hasLoopedOnce, setHasLoopedOnce] = useState(circularFromStart);
  const timeout = useRef(null);

  useEffect(() => {
    let isMounted = true;
    const interval = setInterval(() => {
      if (!isMounted) return;
      setIsTransitioning(true);
      timeout.current = setTimeout(() => {
        setIsTransitioning(false);
        setActiveStep((prev) => {
          const next = (prev + 1) % steps.length;
          // Mark that we have completed at least one full loop
          if (next === 0 && prev === steps.length - 1) {
            setHasLoopedOnce(true);
          }
          return next;
        });
      }, 300);
    }, 3000);

    return () => {
      clearInterval(interval);
      setIsTransitioning(false);
      clearTimeout(timeout.current);
      isMounted = false;
    };
  }, [steps.length]);

  return (
    <div className="loading-container" style={style}>
      {/* Icons Container */}
      <div className="icons-wrapper">
        <div className="carousel-container">
          {steps.map((step, index) => {
            const isActive = index === activeStep;

            const lastIndex = steps.length - 1;

            let isPrevious = false;
            let isNext = false;

            if (steps.length > 1) {
              if (!hasLoopedOnce) {
                // First loop:
                // - step 0: only next (1)
                // - middle steps: previous and next
                // - last step: previous and also "next" as the first item (0)
                if (activeStep > 0 && index === activeStep - 1) {
                  isPrevious = true;
                }
                if (activeStep < lastIndex && index === activeStep + 1) {
                  isNext = true;
                } else if (activeStep === lastIndex && index === 0) {
                  // When on the last step during the first loop,
                  // show what comes next (the first step) on the right.
                  isNext = true;
                }
              } else {
                // After first full loop: circular previous/next
                const previousIndex =
                  (activeStep - 1 + steps.length) % steps.length;
                const nextIndex = (activeStep + 1) % steps.length;
                isPrevious = index === previousIndex;
                isNext = index === nextIndex;
              }
            }

            const isVisible = isActive || isPrevious || isNext;

            // Calculate transform offset
            // Active icon: 70px, Previous/Next: 50px, Gap: 8px
            // Distance from center: (70/2) + 8 + (50/2) = 35 + 8 + 25 = 68px
            let offset = 0;
            if (isPrevious) {
              offset = -68; // -(active_width/2 + gap + prev_width/2)
            } else if (isNext) {
              offset = 68; // +(active_width/2 + gap + next_width/2)
            }

            return (
              <div
                key={step.id}
                className={`icon-container ${isVisible ? "visible" : "hidden"} ${isActive ? "active" : isPrevious ? "previous" : isNext ? "next" : "inactive"}`}
                style={{
                  transform: `translateX(calc(-50% + ${offset}px))`,
                }}
              >
                <div className="icon-wrapper">
                  <SvgColor width={24}
                    // @ts-ignore
                    src={step.icon}
                    className="icon-image"
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Active Step Info */}
      <div className="step-info">
        <h2
          key={`title-${activeStep}`}
          className={`step-title ${isTransitioning ? "fade-out" : ""}`}
        >
          {steps[activeStep].title}
        </h2>
        <p
          key={`desc-${activeStep}`}
          className={`step-description ${isTransitioning ? "fade-out" : ""}`}
        >
          {steps[activeStep].description}
        </p>
      </div>
    </div>
  );
};

export default LoadingStages;

LoadingStages.propTypes = {
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
      title: PropTypes.string.isRequired,
      description: PropTypes.string.isRequired,
      icon: PropTypes.string.isRequired,
    }),
  ).isRequired,
  initialStep: PropTypes.number,
  circularFromStart: PropTypes.bool,
  style: PropTypes.object,
};
