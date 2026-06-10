import React, { createContext, useContext, useState } from 'react';
import { motion } from 'motion/react';
import { cn } from '@/lib/utils';

// Adapted from Aceternity UI's Sidebar (https://ui.aceternity.com/components/sidebar):
// a desktop rail that collapses to icons and expands on hover. Themed to the app's
// CSS variables and wired for react-router (links call onClick instead of navigating
// via <a href>). The app keeps its own mobile nav, so the Aceternity MobileSidebar
// is intentionally omitted and the desktop rail shows from `lg` up.

interface SidebarContextProps {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
  animate: boolean;
}

const SidebarContext = createContext<SidebarContextProps | undefined>(undefined);

export const useSidebar = () => {
  const context = useContext(SidebarContext);
  if (!context) {
    throw new Error('useSidebar must be used within a Sidebar');
  }
  return context;
};

export const SidebarProvider = ({
  children,
  open: openProp,
  setOpen: setOpenProp,
  animate = true,
}: {
  children: React.ReactNode;
  open?: boolean;
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  animate?: boolean;
}) => {
  const [openState, setOpenState] = useState(false);

  const open = openProp !== undefined ? openProp : openState;
  const setOpen = setOpenProp !== undefined ? setOpenProp : setOpenState;

  return (
    <SidebarContext.Provider value={{ open, setOpen, animate }}>
      {children}
    </SidebarContext.Provider>
  );
};

export const Sidebar = ({
  children,
  open,
  setOpen,
  animate,
}: {
  children: React.ReactNode;
  open?: boolean;
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  animate?: boolean;
}) => {
  return (
    <SidebarProvider open={open} setOpen={setOpen} animate={animate}>
      {children}
    </SidebarProvider>
  );
};

export const SidebarBody = (props: React.ComponentProps<typeof motion.div>) => {
  return <DesktopSidebar {...props} />;
};

export const DesktopSidebar = ({
  className,
  children,
  ...props
}: React.ComponentProps<typeof motion.div>) => {
  const { open, setOpen, animate } = useSidebar();
  return (
    <motion.div
      className={cn(
        'hidden h-full w-[300px] shrink-0 flex-col border-r border-line bg-surface px-4 py-6 lg:flex',
        className,
      )}
      animate={{
        width: animate ? (open ? '300px' : '76px') : '300px',
      }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      {...props}
    >
      {children}
    </motion.div>
  );
};

interface SidebarLinkProps {
  label: string;
  icon: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}

export const SidebarLink = ({ label, icon, active, onClick, className }: SidebarLinkProps) => {
  const { open, animate } = useSidebar();
  const expanded = animate ? open : true;
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      className={cn(
        'group/sidebar flex w-full items-center gap-3 rounded-xl py-2.5 text-left text-sm font-medium transition-all duration-150',
        // Center the icon when collapsed; pad/left-align once the label is shown.
        expanded ? 'px-3' : 'justify-center px-0',
        active ? 'bg-accent-soft text-accent' : 'text-secondary hover:bg-hover hover:text-primary',
        className,
      )}
    >
      <span className={cn('shrink-0', active ? 'text-accent' : 'text-muted')}>{icon}</span>
      <motion.span
        animate={{
          display: expanded ? 'inline-block' : 'none',
          opacity: expanded ? 1 : 0,
        }}
        className="!m-0 inline-block whitespace-pre !p-0 transition duration-150 group-hover/sidebar:translate-x-1"
      >
        {label}
      </motion.span>
    </button>
  );
};
