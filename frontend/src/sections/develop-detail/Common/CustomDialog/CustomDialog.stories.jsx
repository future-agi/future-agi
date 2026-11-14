import React from "react";
import CustomDialog from "./CustomDialog";
import logger from "src/utils/logger";

const meta = {
  component: CustomDialog,
  title: "UI Components/CustomDialog",
};

export default meta;

const Template = (args) => {
  const [open, setOpen] = React.useState(false);

  const handleOpen = () => {
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleAction = () => {
    logger.debug("Action button clicked");
  };

  return (
    <div>
      <button onClick={handleOpen}>Open Dialog</button>
      <CustomDialog
        open={open}
        onClose={handleClose}
        title={args.title}
        preTitleIcon={args.preTitleIcon}
        actionButton={args.actionButton}
        isData={args.isData}
        onClickAction={handleAction}
        loading={args.loading}
        color={args.color}
        className={args.className}
      >
        {args.children}
      </CustomDialog>
    </div>
  );
};

export const Default = Template.bind({});

Default.args = {
  title: "Custom Dialog",
  actionButton: "Submit",
  children: <p>This is a custom dialog component.</p>,
};

export const WithLoadingButton = Template.bind({});

WithLoadingButton.args = {
  title: "Custom Dialog with Loading Button",
  preTitleIcon: "solar:info-circle-linear",
  actionButton: "Loading Button",
  loading: true,
  children: <p>This is a custom dialog component with a loading button.</p>,
  color: "secondary",
};

export const WithTitleIcon = Template.bind({});

WithTitleIcon.args = {
  title: "Custom Dialog with Icon",
  preTitleIcon: "solar:info-circle-linear",
  actionButton: "Submit",
  children: <p>This is a custom dialog component with an icon.</p>,
  color: "error",
};

export const WithDisabledButton = Template.bind({});

WithDisabledButton.args = {
  title: "Custom Dialog with Disabled Button",
  preTitleIcon: "solar:info-circle-linear",
  actionButton: "Disabled Button",
  isData: false,
  children: <p>This is a custom dialog component with a disabled button.</p>,
  color: "success",
};
