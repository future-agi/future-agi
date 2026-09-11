/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
  Alert,
  MenuItem,
  Chip,
  IconButton,
  Button,
  Divider,
} from "@mui/material";
import { Icon } from "@iconify/react";

const AUTH_TYPES = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer Token" },
  { value: "api_key", label: "API Key" },
];

const A2AConfigTab = ({ a2a, onChange }) => {
  const config = a2a || {};
  const card = config.card || {};
  const agents = config.agents || {};

  const [newAgentName, setNewAgentName] = useState("");

  const update = (key, value) => {
    onChange({ ...config, [key]: value });
  };

  const handleCardChange = (field, value) => {
    update("card", { ...card, [field]: value });
  };

  const handleAddAgent = () => {
    if (!newAgentName.trim()) return;
    const name = newAgentName.trim();
    if (agents[name]) return;
    update("agents", {
      ...agents,
      [name]: { url: "", auth: { type: "none" }, description: "", skills: [] },
    });
    setNewAgentName("");
  };

  const handleAgentChange = (name, field, value) => {
    update("agents", {
      ...agents,
      [name]: { ...agents[name], [field]: value },
    });
  };

  const handleRemoveAgent = (name) => {
    const updated = { ...agents };
    delete updated[name];
    update("agents", updated);
  };

  const handleAgentAuthChange = (name, field, value) => {
    const agent = agents[name];
    const auth = agent.auth || { type: "none" };
    update("agents", {
      ...agents,
      [name]: { ...agent, auth: { ...auth, [field]: value } },
    });
  };

  const handleAddSkill = (agentName) => {
    const agent = agents[agentName];
    const skills = agent.skills || [];
    handleAgentChange(agentName, "skills", [
      ...skills,
      { id: `skill_${skills.length + 1}`, name: "", description: "" },
    ]);
  };

  const handleSkillChange = (agentName, idx, field, value) => {
    const agent = agents[agentName];
    const skills = [...(agent.skills || [])];
    skills[idx] = { ...skills[idx], [field]: value };
    handleAgentChange(agentName, "skills", skills);
  };

  const handleRemoveSkill = (agentName, idx) => {
    const agent = agents[agentName];
    const skills = [...(agent.skills || [])];
    skills.splice(idx, 1);
    handleAgentChange(agentName, "skills", skills);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="mdi:robot-outline" width={24} />
        <Typography variant="h6">A2A (Agent-to-Agent)</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Configure per-organization A2A protocol settings. Customize your agent
        card and register external A2A agents with their endpoints,
        authentication, and skills.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={config.enabled || false}
            onChange={(e) => update("enabled", e.target.checked)}
          />
        }
        label="Enable per-org A2A"
        sx={{ mb: 2 }}
      />

      {config.enabled && (
        <Stack spacing={3}>
          {/* ===== AGENT CARD ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="bold" sx={{ mb: 2 }}>
              Agent Card
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Customize how Agent Command Center identifies itself as an A2A
              agent for this organization.
            </Typography>
            <Stack spacing={2}>
              <TextField
                size="small"
                label="Name"
                value={card.name || ""}
                onChange={(e) => handleCardChange("name", e.target.value)}
                fullWidth
                placeholder="e.g. Agent Command Center Gateway"
              />
              <TextField
                size="small"
                label="Description"
                value={card.description || ""}
                onChange={(e) =>
                  handleCardChange("description", e.target.value)
                }
                fullWidth
                multiline
                rows={2}
                placeholder="e.g. LLM gateway with multi-provider routing"
              />
              <TextField
                size="small"
                label="Version"
                value={card.version || ""}
                onChange={(e) => handleCardChange("version", e.target.value)}
                sx={{ width: 200 }}
                placeholder="e.g. 1.0.0"
              />
            </Stack>
          </Paper>

          <Divider />

          {/* ===== AGENT REGISTRY ===== */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="subtitle1" fontWeight="bold">
                External Agent Registry
              </Typography>
            </Stack>

            {Object.keys(agents).length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                No external agents registered. Add agents to connect to other
                A2A-compatible services.
              </Typography>
            )}

            {Object.entries(agents).map(([name, agent]) => (
              <Paper key={name} variant="outlined" sx={{ p: 2, mb: 2 }}>
                <Stack spacing={2}>
                  <Stack
                    direction="row"
                    justifyContent="space-between"
                    alignItems="center"
                  >
                    <Chip
                      label={name}
                      color="primary"
                      variant="outlined"
                      size="small"
                    />
                    <IconButton
                      size="small"
                      onClick={() => handleRemoveAgent(name)}
                    >
                      <Icon
                        icon="mdi:delete-outline"
                        width={18}
                        color="#d32f2f"
                      />
                    </IconButton>
                  </Stack>

                  <TextField
                    size="small"
                    label="URL"
                    value={agent.url || ""}
                    onChange={(e) =>
                      handleAgentChange(name, "url", e.target.value)
                    }
                    fullWidth
                    placeholder="https://agent.example.com"
                  />

                  <TextField
                    size="small"
                    label="Description"
                    value={agent.description || ""}
                    onChange={(e) =>
                      handleAgentChange(name, "description", e.target.value)
                    }
                    fullWidth
                    placeholder="What does this agent do?"
                  />

                  {/* Auth */}
                  <Stack direction="row" spacing={1.5} alignItems="center">
                    <TextField
                      select
                      size="small"
                      label="Auth Type"
                      value={agent.auth?.type || "none"}
                      onChange={(e) =>
                        handleAgentAuthChange(name, "type", e.target.value)
                      }
                      sx={{ width: 160 }}
                    >
                      {AUTH_TYPES.map((at) => (
                        <MenuItem key={at.value} value={at.value}>
                          {at.label}
                        </MenuItem>
                      ))}
                    </TextField>

                    {agent.auth?.type === "bearer" && (
                      <TextField
                        size="small"
                        label="Token"
                        type="password"
                        value={agent.auth?.token || ""}
                        onChange={(e) =>
                          handleAgentAuthChange(name, "token", e.target.value)
                        }
                        sx={{ flex: 1 }}
                        placeholder="Bearer token"
                      />
                    )}

                    {agent.auth?.type === "api_key" && (
                      <>
                        <TextField
                          size="small"
                          label="Header"
                          value={agent.auth?.header || ""}
                          onChange={(e) =>
                            handleAgentAuthChange(
                              name,
                              "header",
                              e.target.value,
                            )
                          }
                          sx={{ width: 180 }}
                          placeholder="e.g. X-API-Key"
                        />
                        <TextField
                          size="small"
                          label="Token"
                          type="password"
                          value={agent.auth?.token || ""}
                          onChange={(e) =>
                            handleAgentAuthChange(name, "token", e.target.value)
                          }
                          sx={{ flex: 1 }}
                          placeholder="API key value"
                        />
                      </>
                    )}
                  </Stack>

                  {/* Skills */}
                  <Box>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      sx={{ mb: 1 }}
                    >
                      <Typography variant="body2" fontWeight="bold">
                        Skills
                      </Typography>
                      <Button
                        size="small"
                        variant="text"
                        onClick={() => handleAddSkill(name)}
                        startIcon={<Icon icon="mdi:plus" width={14} />}
                      >
                        Add Skill
                      </Button>
                    </Stack>

                    {(agent.skills || []).length === 0 && (
                      <Typography variant="caption" color="text.secondary">
                        No skills defined.
                      </Typography>
                    )}

                    {(agent.skills || []).map((skill, idx) => (
                      <Paper key={idx} variant="outlined" sx={{ p: 1, mb: 1 }}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <TextField
                            size="small"
                            label="ID"
                            value={skill.id || ""}
                            onChange={(e) =>
                              handleSkillChange(name, idx, "id", e.target.value)
                            }
                            sx={{ width: 120 }}
                          />
                          <TextField
                            size="small"
                            label="Name"
                            value={skill.name || ""}
                            onChange={(e) =>
                              handleSkillChange(
                                name,
                                idx,
                                "name",
                                e.target.value,
                              )
                            }
                            sx={{ width: 160 }}
                          />
                          <TextField
                            size="small"
                            label="Description"
                            value={skill.description || ""}
                            onChange={(e) =>
                              handleSkillChange(
                                name,
                                idx,
                                "description",
                                e.target.value,
                              )
                            }
                            sx={{ flex: 1 }}
                          />
                          <IconButton
                            size="small"
                            onClick={() => handleRemoveSkill(name, idx)}
                          >
                            <Icon
                              icon="mdi:delete-outline"
                              width={16}
                              color="#d32f2f"
                            />
                          </IconButton>
                        </Stack>
                      </Paper>
                    ))}
                  </Box>
                </Stack>
              </Paper>
            ))}

            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField
                size="small"
                placeholder="Agent name"
                value={newAgentName}
                onChange={(e) => setNewAgentName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddAgent();
                  }
                }}
                sx={{ width: 200 }}
              />
              <Button size="small" variant="outlined" onClick={handleAddAgent}>
                Add Agent
              </Button>
            </Stack>
          </Paper>
        </Stack>
      )}
    </Box>
  );
};

export default A2AConfigTab;
