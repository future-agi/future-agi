import PropTypes from "prop-types";
import { Component } from "react";

const items = (list) => [...list.querySelectorAll("[data-message-id]")];
const topOf = (node) => node.getBoundingClientRect().top;

/**
 * Holds the reader's place when older messages or steps load above what they are reading,
 * and fades those in. A class, since only `getSnapshotBeforeUpdate` can
 * measure the list right before it changes.
 */
export default class KeepPlace extends Component {
  getSnapshotBeforeUpdate(prev) {
    const list = this.props.listRef.current;
    if (prev.from === this.props.from || !list) return null;
    const box = list.getBoundingClientRect();
    const shown = items(list);
    const inView = shown
      .filter((node) => {
        const { top, bottom } = node.getBoundingClientRect();
        return top < box.bottom && bottom > box.top;
      })
      .map((node) => ({ id: node.dataset.messageId, top: topOf(node) }));
    return {
      ids: new Set(shown.map((node) => node.dataset.messageId)),
      // What is read first holds still, or the latest for a reader who follows it.
      inView: this.props.following ? inView.reverse() : inView,
    };
  }

  componentDidUpdate(prev, state, snapshot) {
    if (!snapshot) return;
    const list = this.props.listRef.current;
    const byId = new Map(
      items(list).map((node) => [node.dataset.messageId, node]),
    );
    const kept = snapshot.inView.find(({ id }) => byId.has(id));
    // Another conversation: nothing on screen is still there.
    if (!kept) return;
    list.scrollTop += topOf(byId.get(kept.id)) - kept.top;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    byId.forEach((node, id) => {
      if (!snapshot.ids.has(id)) {
        node.animate?.([{ opacity: 0 }, { opacity: 1 }], this.props.duration);
      }
    });
  }

  render() {
    return this.props.children;
  }
}

KeepPlace.propTypes = {
  listRef: PropTypes.object.isRequired,
  // Where what is held starts: it changes when older ones load.
  from: PropTypes.string,
  // Whether the list follows the latest.
  following: PropTypes.bool,
  duration: PropTypes.number,
  children: PropTypes.node,
};
