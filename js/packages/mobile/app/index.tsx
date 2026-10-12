import { useEffect, useState } from "react";
import { Button, Text, View } from "react-native";
import { api, signOut, storedToken, useSignIn } from "../auth";

interface Me { email: string | null; organizations: { id: string; name: string; role: string }[] }

export default function Home() {
  const signIn = useSignIn();
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => { storedToken().then(setToken); }, []);
  useEffect(() => {
    if (token) api<Me>("/me", token).then(setMe, () => { void signOut(); setToken(null); });
  }, [token]);

  if (!token) return <View style={{ padding: 24 }}><Button title="Sign in" onPress={async () => setToken(await signIn())} /></View>;
  return (
    <View style={{ padding: 24 }}>
      <Text>{me ? `Signed in as ${me.email}` : "Loading…"}</Text>
      {me?.organizations.map((o) => <Text key={o.id}>{o.name} ({o.role})</Text>)}
      <Button title="Sign out" onPress={async () => { await signOut(); setToken(null); setMe(null); }} />
    </View>
  );
}
