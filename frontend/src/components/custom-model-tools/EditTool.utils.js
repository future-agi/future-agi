export function getDefaultValues(editTool) {
  const defaultJson = `{
  "type": "function",
  "function": {
      "name": "",
      "description": "",
      "parameters": {
          "type": "object",
          "properties": {},
          "required": []
      }
  }
}`;

  const defaultYaml = `
  type: function
  function:
    name:
    description:
    parameters:
      type: object
      properties: {}
      required: []
  `;

  if (editTool) {
    return {
      name: editTool?.name || "",
      description: editTool?.description || "",
      config_type: editTool?.config_type,
      inputSchema: {
        json:
          editTool?.config_type === "json" && editTool?.config
            ? JSON.stringify(editTool?.config, null, 2)
            : defaultJson,
        yaml:
          editTool?.config_type === "yaml" && editTool?.yaml_config
            ? editTool?.yaml_config
            : defaultYaml,
      },
    };
  }

  return {
    name: "",
    description: "",
    config_type: "json",
    inputSchema: {
      json: defaultJson,
      yaml: defaultYaml,
    },
  };
}
