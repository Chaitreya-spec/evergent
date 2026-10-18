import { HomeView } from "@/components/HomeView";

export default async function HomePage(props: PageProps<"/">) {
  const { focus } = await props.searchParams;
  return <HomeView autoFocus={focus === "1"} />;
}
