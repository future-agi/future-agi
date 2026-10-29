import { NodeIcon } from "./NodeIcon";
import { NodeMetadata } from "./NodeMetadata";
import "./TreeNode.css";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

// Constants for alignment - icon is 20px, padding is 16px
// Center of icon from content edge: 16px padding + 10px (half of 20px icon) = 26px
const ICON_CENTER_OFFSET = 26;
const DEPTH_INDENT = 40;

/**
 * @param {{
 *  node: import("./types").TreeNodeData;
 *  isLast?: boolean;
 *  depth?: number;
 *  parentHasMoreSiblings?: boolean[];
 *  selectedId?: string | null;
 *  expandedIds: Set<string>;
 *  onSelect?: (id: string) => void;
 *  onToggleExpand: (id: string) => void;
 *  customNodeComponent?: React.ComponentType<any>;
 * }} props
 */
export const TreeNode = ({
  node,
  isLast = false,
  depth = 0,
  parentHasMoreSiblings = [],
  selectedId,
  expandedIds,
  onSelect,
  onToggleExpand,
  customNodeComponent: CustomNodeComponent,
}) => {
  const hasChildren = Boolean(node.children && node.children.length > 0);
  const isSelected = selectedId === node.id;
  const isExpanded = expandedIds.has(node.id);

  const handleClick = () => {
    onSelect?.(node.id);
  };

  const handleToggle = (e) => {
    e.stopPropagation();
    onToggleExpand(node.id);
  };

  // If custom node component is provided, use it
  if (CustomNodeComponent) {
    return (
      <div className="tree-node">
        {/* Connecting lines for nested items */}
        {depth > 0 && (
          <>
            {/* Vertical lines from parent levels */}
            {parentHasMoreSiblings.map(
              (hasMore, index) =>
                hasMore && (
                  <div
                    key={index}
                    className="tree-node__vertical-line"
                    style={{
                      left: `${index * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
                      top: `-${1.55 * ICON_CENTER_OFFSET}px`,
                    }}
                  />
                ),
            )}
            {/* Horizontal connector line */}
            <div
              className="tree-node__horizontal-line"
              style={{
                left: `${(depth - 1) * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
                top: `${0.7 * ICON_CENTER_OFFSET}px`,
                width: `${DEPTH_INDENT}px`,
              }}
            />
            {/* Vertical line to this node */}
            <div
              className="tree-node__connector-line"
              style={{
                left: `${(depth - 1) * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
                top: isLast
                  ? `-${1.5 * ICON_CENTER_OFFSET}px`
                  : ICON_CENTER_OFFSET,
                height: isLast ? `${2.2 * ICON_CENTER_OFFSET}px` : "100%",
              }}
            />
          </>
        )}

        <CustomNodeComponent
          node={node}
          depth={depth}
          isSelected={isSelected}
          isExpanded={isExpanded}
          hasChildren={hasChildren}
          onSelect={handleClick}
          onToggle={handleToggle}
        />

        {/* Children */}
        {hasChildren && isExpanded && (
          <div className="tree-node__children">
            {node.children.map((child, index) => (
              <div key={child.id} className="tree-node__child">
                <TreeNode
                  node={child}
                  isLast={index === node.children.length - 1}
                  depth={depth + 1}
                  parentHasMoreSiblings={[
                    ...parentHasMoreSiblings,
                    index !== node.children.length - 1,
                  ]}
                  selectedId={selectedId}
                  expandedIds={expandedIds}
                  onSelect={onSelect}
                  onToggleExpand={onToggleExpand}
                  customNodeComponent={CustomNodeComponent}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Default node rendering
  const contentClasses = [
    "tree-node__content",
    (hasChildren || onSelect) && "tree-node__content--clickable",
    isSelected && "tree-node__content--selected",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="tree-node">
      {/* Connecting lines for nested items */}
      {depth > 0 && (
        <>
          {/* Vertical lines from parent levels */}
          {parentHasMoreSiblings.map(
            (hasMore, index) =>
              hasMore && (
                <div
                  key={index}
                  className="tree-node__vertical-line"
                  style={{
                    left: `${index * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
                    top: -ICON_CENTER_OFFSET,
                  }}
                />
              ),
          )}
          {/* Horizontal connector line */}
          <div
            className="tree-node__horizontal-line"
            style={{
              left: `${(depth - 1) * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
              top: `${ICON_CENTER_OFFSET}px`,
              width: `${DEPTH_INDENT}px`,
            }}
          />
          {/* Vertical line to this node */}
          <div
            className="tree-node__connector-line"
            style={{
              left: `${(depth - 1) * DEPTH_INDENT + ICON_CENTER_OFFSET}px`,
              top: isLast ? `-${ICON_CENTER_OFFSET}px` : ICON_CENTER_OFFSET,
              height: isLast ? `${2 * ICON_CENTER_OFFSET}px` : "100%",
            }}
          />
        </>
      )}

      {/* Node content */}
      <div
        className={contentClasses}
        style={{ marginLeft: `${depth * DEPTH_INDENT}px` }}
        onClick={handleClick}
      >
        <NodeIcon type={node.type} />
        <div className="tree-node__info">
          <span className="tree-node__name">{node.name}</span>
          <NodeMetadata
            duration={node.duration}
            tokens={node.tokens}
            cost={node.cost}
          />
        </div>
        {hasChildren && (
          <button className="tree-node__toggle" onClick={handleToggle}>
            <Iconify icon="tabler:chevron-up" width={16} color="black.1000" />
          </button>
        )}
      </div>

      {/* Children */}
      {hasChildren && isExpanded && (
        <div className="tree-node__children">
          {node.children.map((child, index) => (
            <div key={child.id} className="tree-node__child">
              <TreeNode
                node={child}
                isLast={index === node.children.length - 1}
                depth={depth + 1}
                parentHasMoreSiblings={[
                  ...parentHasMoreSiblings,
                  index !== node.children.length - 1,
                ]}
                selectedId={selectedId}
                expandedIds={expandedIds}
                onSelect={onSelect}
                onToggleExpand={onToggleExpand}
                customNodeComponent={CustomNodeComponent}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

TreeNode.propTypes = {
  node: PropTypes.object.isRequired,
  isLast: PropTypes.bool,
  depth: PropTypes.number,
  parentHasMoreSiblings: PropTypes.array,
  selectedId: PropTypes.string,
  expandedIds: PropTypes.instanceOf(Set),
  onSelect: PropTypes.func,
  onToggleExpand: PropTypes.func.isRequired,
  customNodeComponent: PropTypes.elementType,
};
