import "./globals.css";
export const metadata = {
  title: "Red Compass | Construction approvals",
  description:
    "A fictional construction approvals company. An OpenShell agent demonstration.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
