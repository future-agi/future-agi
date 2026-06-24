import { useState, useEffect, useRef } from "react";
import PropTypes from "prop-types";

export default function VirtualCard({
  height = 120,
  children,
  rootMargin = "200px",
  style,
}) {
  const [isVisible, setIsVisible] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setIsVisible(true);
      },
      { rootMargin },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [rootMargin]);

  return (
    <div ref={ref} style={{ minHeight: height, height: "100%", ...style }}>
      {isVisible ? children : null}
    </div>
  );
}

VirtualCard.propTypes = {
  height: PropTypes.number,
  children: PropTypes.node,
  rootMargin: PropTypes.string,
  style: PropTypes.object,
};
