#!/bin/bash

# Branch creation helper script
# Usage: ./scripts/create-branch.sh <type> <description> [ticket-id]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display usage
show_usage() {
    echo -e "${BLUE}Branch Creation Helper${NC}"
    echo ""
    echo "Usage: $0 <type> <description> [ticket-id]"
    echo ""
    echo "Types (matches Conventional Commits prefixes in CONTRIBUTING.md):"
    echo "  feat      - New features or enhancements"
    echo "  fix       - Bug fixes"
    echo "  chore     - Maintenance tasks, build changes"
    echo "  docs      - Documentation updates"
    echo "  refactor  - Code refactoring"
    echo "  test      - Adding or updating tests"
    echo "  perf      - Performance improvements"
    echo ""
    echo "Examples:"
    echo "  $0 feat user-authentication AUTH-123"
    echo "  $0 fix login-error BUG-456"
    echo "  $0 docs update-readme"
    echo ""
}

# Function to validate branch type
validate_type() {
    local type=$1
    valid_types=("feat" "fix" "chore" "docs" "refactor" "test" "perf")
    
    for valid_type in "${valid_types[@]}"; do
        if [[ "$type" == "$valid_type" ]]; then
            return 0
        fi
    done
    return 1
}

# Function to validate description format
validate_description() {
    local description=$1
    
    # Check if description contains only allowed characters
    if [[ ! $description =~ ^[a-zA-Z0-9-]+$ ]]; then
        echo -e "${RED}Error: Description can only contain letters, numbers, and hyphens${NC}"
        return 1
    fi
    
    # Check if description starts and ends with alphanumeric
    if [[ ! $description =~ ^[a-zA-Z0-9].*[a-zA-Z0-9]$ ]] && [[ ${#description} -gt 1 ]]; then
        echo -e "${RED}Error: Description must start and end with letters or numbers${NC}"
        return 1
    fi
    
    return 0
}

# Function to create branch name
create_branch_name() {
    local type=$1
    local description=$2
    local ticket_id=$3
    
    if [[ -n "$ticket_id" ]]; then
        echo "${type}/${ticket_id}-${description}"
    else
        echo "${type}/${description}"
    fi
}

# Main script
main() {
    # Check if git is available
    if ! command -v git &> /dev/null; then
        echo -e "${RED}Error: git is not installed or not in PATH${NC}"
        exit 1
    fi
    
    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        echo -e "${RED}Error: Not in a git repository${NC}"
        exit 1
    fi
    
    # Check arguments
    if [[ $# -lt 2 ]] || [[ $# -gt 3 ]]; then
        show_usage
        exit 1
    fi
    
    local type=$1
    local description=$2
    local ticket_id=$3
    
    # Validate type
    if ! validate_type "$type"; then
        echo -e "${RED}Error: Invalid branch type '$type'${NC}"
        show_usage
        exit 1
    fi
    
    # Validate description
    if ! validate_description "$description"; then
        exit 1
    fi
    
    # Create branch name
    branch_name=$(create_branch_name "$type" "$description" "$ticket_id")
    
    # Check if branch already exists
    if git show-ref --verify --quiet refs/heads/"$branch_name"; then
        echo -e "${RED}Error: Branch '$branch_name' already exists${NC}"
        exit 1
    fi
    
    # Show what we're about to do
    echo -e "${BLUE}Creating branch:${NC} ${GREEN}$branch_name${NC}"
    
    # Get current branch
    current_branch=$(git rev-parse --abbrev-ref HEAD)
    echo -e "${BLUE}From:${NC} $current_branch"
    
    # Ask for confirmation
    read -p "Continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Cancelled${NC}"
        exit 0
    fi
    
    # Create and checkout the branch
    if git checkout -b "$branch_name"; then
        echo -e "${GREEN}✅ Successfully created and switched to branch '$branch_name'${NC}"
        echo ""
        echo -e "${BLUE}Next steps:${NC}"
        echo "1. Make your changes"
        echo "2. Commit your changes: git add . && git commit -m 'your message'"
        echo "3. Push your branch: git push -u origin $branch_name"
        echo "4. Create a pull request"
    else
        echo -e "${RED}Error: Failed to create branch${NC}"
        exit 1
    fi
}

# Run the script
main "$@" 