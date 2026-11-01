import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Read-only screen observer for the compatibility runner's extended client checks.
 *
 * <p>It registers no class transformer and changes nothing in the game. A daemon thread waits for
 * the client to exist, then queues a read on the client's own executor ({@link Executor#execute},
 * a JDK method), so every read happens on the render thread between frames. Each time what is on
 * screen changes, and at least once a second while reads succeed (a heartbeat that lets the runner
 * tell an unchanged screen from a stalled one), one JSON line is appended to the output file: the
 * screen class and title, the GUI
 * and window sizes, and every widget's class, message, bounds, and active/visible state. Selection
 * lists also report their rows, and for screens that are not obfuscated (Mod Menu's, the mod's own)
 * the field holding each widget is named.
 *
 * <p>Minecraft member names are intermediary names, supplied by the runner in a properties file it
 * derives from the pinned client mappings and intermediary jar. A required member that cannot be
 * found makes the observer report {@code "status":"unavailable"} and stop; the runner then treats
 * every extended step as blocked, never as a pass or a failure.
 */
public final class ScreenObserver implements Runnable {

    private static final String[] REQUIRED = {
        "method.Minecraft.getInstance", "method.Minecraft.getWindow", "field.Minecraft.screen",
        "method.Window.getScreenWidth", "method.Window.getScreenHeight", "method.Window.getGuiScaledWidth",
        "method.Window.getGuiScaledHeight", "method.Screen.getTitle", "method.Screen.children",
        "method.AbstractWidget.getMessage", "method.AbstractWidget.getX", "method.AbstractWidget.getY",
        "method.AbstractWidget.getWidth", "method.AbstractWidget.getHeight", "field.AbstractWidget.active",
        "field.AbstractWidget.visible", "method.Component.getString",
    };
    private static final int MAX_WIDGETS = 200;
    private static final int MAX_ROWS = 64;
    /** The client drops queued tasks when it leaves a world; a read pending this long is queued again. */
    private static final long STALE_READ_MS = 2000;
    /** An unchanged screen is still written this often, so the runner can tell "unchanged" from "silent". */
    private static final long HEARTBEAT_MS = 1000;

    private final Properties names;
    private final Instrumentation instrumentation;
    private final String output;
    private final long intervalMs;
    private final ConcurrentLinkedQueue<String> snapshots = new ConcurrentLinkedQueue<>();
    private final AtomicBoolean pending = new AtomicBoolean();
    private final Map<String, Method> methods = new java.util.HashMap<>();
    private final Map<String, Field> fields = new java.util.HashMap<>();
    private ClassLoader gameLoader;
    private Object client;
    private Class<?> widgetClass;
    private Class<?> listClass;
    private Class<?> editBoxClass;
    private String last = "";
    private long sequence;

    private ScreenObserver(Properties names, Instrumentation instrumentation) {
        this.names = names;
        this.instrumentation = instrumentation;
        this.output = names.getProperty("out");
        this.intervalMs = Long.parseLong(names.getProperty("interval_ms", "200"));
    }

    public static void premain(String args, Instrumentation instrumentation) throws IOException {
        Properties names = new Properties();
        try (InputStream in = Files.newInputStream(Paths.get(args))) {
            names.load(in);
        }
        Thread thread = new Thread(new ScreenObserver(names, instrumentation), "egc-screen-observer");
        thread.setDaemon(true);
        thread.start();
    }

    @Override
    public void run() {
        try {
            while (client == null) {
                Thread.sleep(intervalMs);
                client = findClient();
            }
            List<String> missing = resolveMembers();
            if (!missing.isEmpty() || !(client instanceof Executor)) {
                if (!(client instanceof Executor)) {
                    missing.add("client is not a java.util.concurrent.Executor");
                }
                write("{\"status\":\"unavailable\",\"missing\":" + array(missing) + "}");
                return;
            }
            Executor executor = (Executor) client;
            long pendingSince = 0;
            long lastWrite = 0;
            while (true) {
                long now = System.currentTimeMillis();
                if (pending.get() && now - pendingSince > STALE_READ_MS) {
                    pending.set(false); // the queued read was dropped; a late one would only repeat a snapshot
                }
                if (pending.compareAndSet(false, true)) {
                    pendingSince = now;
                    executor.execute(this::snapshot);
                }
                for (String line; (line = snapshots.poll()) != null; ) {
                    if (!line.equals(last) || now - lastWrite >= HEARTBEAT_MS) {
                        last = line;
                        lastWrite = now;
                        write(line);
                    }
                }
                Thread.sleep(intervalMs);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Throwable error) {
            write("{\"status\":\"error\",\"error\":" + quote(error.toString()) + "}");
        }
    }

    /** The client instance once the render thread runs with Knot's class loader and the client exists. */
    private Object findClient() throws ReflectiveOperationException {
        if (gameLoader == null) {
            for (Thread thread : Thread.getAllStackTraces().keySet()) {
                if ("Render thread".equals(thread.getName()) && thread.getContextClassLoader() != null) {
                    gameLoader = thread.getContextClassLoader();
                }
            }
            if (gameLoader == null) {
                return null;
            }
        }
        String name = names.getProperty("class.Minecraft");
        for (Class<?> loaded : instrumentation.getAllLoadedClasses()) {
            if (loaded.getName().equals(name)) {
                Method getInstance = method(loaded, names.getProperty("method.Minecraft.getInstance"), 0);
                return getInstance == null ? null : getInstance.invoke(null);
            }
        }
        return null; // not loaded yet: never load game classes from this thread
    }

    private List<String> resolveMembers() {
        List<String> missing = new ArrayList<>();
        for (String key : names.stringPropertyNames()) {
            if (!key.startsWith("method.") && !key.startsWith("field.")) {
                continue;
            }
            String[] parts = key.split("\\.");
            Class<?> owner = loadedClass(names.getProperty("class." + parts[1]));
            String member = names.getProperty(key);
            if (owner == null) {
                continue; // resolved lazily on the render thread
            }
            if (key.startsWith("method.")) {
                Method found = method(owner, member, parameters(parts[2]));
                if (found != null && java.lang.reflect.Modifier.isStatic(found.getModifiers())) {
                    methods.put(key, found); // static: instance methods are resolved per target class
                }
            } else {
                Field found = field(owner, member);
                if (found != null) {
                    fields.put(key, found);
                }
            }
        }
        for (String key : REQUIRED) {
            if (names.getProperty(key) == null) {
                missing.add(key);
            }
        }
        return missing;
    }

    /** Every observed method takes no argument except the row geometry, which takes a row index. */
    private static int parameters(String mojangName) {
        return "getRowTop".equals(mojangName) || "getRowBottom".equals(mojangName) ? 1 : 0;
    }

    private Class<?> loadedClass(String name) {
        if (name == null) {
            return null;
        }
        for (Class<?> loaded : instrumentation.getAllLoadedClasses()) {
            if (loaded.getName().equals(name)) {
                return loaded;
            }
        }
        return null;
    }

    /** Runs on the render thread. */
    private void snapshot() {
        try {
            snapshots.add(describe());
        } catch (Throwable error) {
            snapshots.add("{\"status\":\"error\",\"error\":" + quote(error.toString()) + "}");
        } finally {
            pending.set(false);
        }
    }

    /**
     * Calls a member by its intermediary name. An instance method is resolved on the target's own
     * class (cached per class), so an override such as a selection list's {@code children()} is the
     * method invoked, not the one first found on the owner class.
     */
    private Object call(String key, Object target, Object... args) throws ReflectiveOperationException {
        String cacheKey = target == null ? key : key + "@" + target.getClass().getName();
        Method method = methods.get(cacheKey);
        if (method == null) {
            String name = names.getProperty(key);
            Class<?> owner = target != null ? target.getClass()
                : gameClass(names.getProperty("class." + key.split("\\.")[1]));
            method = owner == null || name == null ? null : method(owner, name, args.length);
            if (method == null) {
                throw new NoSuchMethodException(key + " on " + (owner == null ? "?" : owner.getName()));
            }
            methods.put(cacheKey, method);
        }
        return method.invoke(target, args);
    }

    private Object read(String key, Object target) throws ReflectiveOperationException {
        Field field = fields.get(key);
        if (field == null) {
            String[] parts = key.split("\\.");
            Class<?> owner = gameClass(names.getProperty("class." + parts[1]));
            String name = names.getProperty(key);
            field = owner == null || name == null ? null : field(owner, name);
            if (field == null) {
                throw new NoSuchFieldException(key);
            }
            fields.put(key, field);
        }
        return field.get(target);
    }

    /** Game classes are resolved on the render thread, where the game loads its own classes. */
    private Class<?> gameClass(String name) {
        if (name == null) {
            return null;
        }
        try {
            return Class.forName(name, false, gameLoader);
        } catch (ClassNotFoundException e) {
            return null;
        }
    }

    private String text(Object component) throws ReflectiveOperationException {
        return component == null ? null : (String) call("method.Component.getString", component);
    }

    private String describe() throws ReflectiveOperationException {
        if (widgetClass == null) {
            widgetClass = gameClass(names.getProperty("class.AbstractWidget"));
            listClass = gameClass(names.getProperty("class.AbstractSelectionList"));
            editBoxClass = gameClass(names.getProperty("class.EditBox"));
        }
        StringBuilder json = new StringBuilder("{\"status\":\"ok\"");
        Object window = call("method.Minecraft.getWindow", client);
        json.append(",\"gui\":{\"scaled_w\":").append(call("method.Window.getGuiScaledWidth", window))
            .append(",\"scaled_h\":").append(call("method.Window.getGuiScaledHeight", window))
            .append(",\"window_w\":").append(call("method.Window.getScreenWidth", window))
            .append(",\"window_h\":").append(call("method.Window.getScreenHeight", window)).append('}');
        Object screen = read("field.Minecraft.screen", client);
        if (screen == null) {
            return json.append(",\"screen\":null}").toString();
        }
        json.append(",\"screen\":{\"class\":").append(quote(screen.getClass().getName()))
            .append(",\"title\":").append(quote(text(call("method.Screen.getTitle", screen)))).append('}');
        List<?> children = (List<?>) call("method.Screen.children", screen);
        Map<Object, String> named = namedFields(screen);
        Object focused = names.getProperty("method.ContainerEventHandler.getFocused") == null ? null
            : safeCall("method.ContainerEventHandler.getFocused", screen);
        StringBuilder widgets = new StringBuilder();
        StringBuilder lists = new StringBuilder();
        for (int i = 0; i < children.size() && i < MAX_WIDGETS; i++) {
            Object child = children.get(i);
            if (listClass != null && listClass.isInstance(child)) {
                appendList(lists, i, child, named.get(child));
            } else if (widgetClass != null && widgetClass.isInstance(child)) {
                appendWidget(widgets, i, child, named.get(child), child == focused);
            }
        }
        json.append(",\"widgets\":[").append(widgets).append("],\"lists\":[").append(lists).append(']');
        String selected = selectedMod(screen);
        if (selected != null) {
            json.append(",\"selected_mod\":").append(quote(selected));
        }
        return json.append('}').toString();
    }

    private Object safeCall(String key, Object target) {
        try {
            return call(key, target);
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    private void appendWidget(StringBuilder out, int index, Object widget, String field, boolean focused)
            throws ReflectiveOperationException {
        if (out.length() > 0) {
            out.append(',');
        }
        out.append("{\"i\":").append(index).append(",\"class\":").append(quote(widget.getClass().getName()))
            .append(",\"message\":").append(quote(text(call("method.AbstractWidget.getMessage", widget))))
            .append(",\"x\":").append(call("method.AbstractWidget.getX", widget))
            .append(",\"y\":").append(call("method.AbstractWidget.getY", widget))
            .append(",\"w\":").append(call("method.AbstractWidget.getWidth", widget))
            .append(",\"h\":").append(call("method.AbstractWidget.getHeight", widget))
            .append(",\"active\":").append(read("field.AbstractWidget.active", widget))
            .append(",\"visible\":").append(read("field.AbstractWidget.visible", widget))
            .append(",\"focused\":").append(focused);
        if (field != null) {
            out.append(",\"field\":").append(quote(field));
        }
        if (editBoxClass != null && editBoxClass.isInstance(widget) && names.getProperty("method.EditBox.getValue") != null) {
            out.append(",\"value\":").append(quote((String) safeCall("method.EditBox.getValue", widget)));
        }
        out.append('}');
    }

    private void appendList(StringBuilder out, int index, Object list, String field) throws ReflectiveOperationException {
        if (out.length() > 0) {
            out.append(',');
        }
        List<?> entries = (List<?>) call("method.Screen.children", list);
        Object selected = names.getProperty("method.AbstractSelectionList.getSelected") == null ? null
            : safeCall("method.AbstractSelectionList.getSelected", list);
        out.append("{\"i\":").append(index).append(",\"class\":").append(quote(list.getClass().getName()))
            .append(",\"entries\":").append(entries.size());
        if (field != null) {
            out.append(",\"field\":").append(quote(field));
        }
        if (widgetClass != null && widgetClass.isInstance(list)) {
            out.append(",\"x\":").append(call("method.AbstractWidget.getX", list))
                .append(",\"y\":").append(call("method.AbstractWidget.getY", list))
                .append(",\"w\":").append(call("method.AbstractWidget.getWidth", list))
                .append(",\"h\":").append(call("method.AbstractWidget.getHeight", list));
        }
        out.append(",\"selected\":").append(selected == null ? -1 : entries.indexOf(selected));
        out.append(",\"rows\":[");
        boolean rowGeometry = names.getProperty("method.AbstractSelectionList.getRowTop") != null
            && names.getProperty("method.AbstractSelectionList.getRowBottom") != null
            && names.getProperty("method.AbstractSelectionList.getRowLeft") != null
            && names.getProperty("method.AbstractSelectionList.getRowWidth") != null;
        for (int row = 0; rowGeometry && row < entries.size() && row < MAX_ROWS; row++) {
            if (row > 0) {
                out.append(',');
            }
            Object entry = entries.get(row);
            int top = ((Number) call("method.AbstractSelectionList.getRowTop", list, row)).intValue();
            int bottom = ((Number) call("method.AbstractSelectionList.getRowBottom", list, row)).intValue();
            out.append("{\"y\":").append(top)
                .append(",\"x\":").append(call("method.AbstractSelectionList.getRowLeft", list))
                .append(",\"w\":").append(call("method.AbstractSelectionList.getRowWidth", list))
                .append(",\"h\":").append(bottom - top)
                .append(",\"class\":").append(quote(entry.getClass().getName()));
            String mod = modId(entry);
            if (mod != null) {
                out.append(",\"mod\":").append(quote(mod));
            }
            out.append('}');
        }
        out.append("]}");
    }

    /** For screens outside net.minecraft, the declared field holding each child, by identity. */
    private Map<Object, String> namedFields(Object screen) {
        Map<Object, String> named = new IdentityHashMap<>();
        for (Class<?> type = screen.getClass(); type != null && !type.getName().startsWith("net.minecraft.");
             type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (field.getType().isPrimitive() || java.lang.reflect.Modifier.isStatic(field.getModifiers())) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    Object value = field.get(screen);
                    if (value != null && !named.containsKey(value)) {
                        named.put(value, field.getName());
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // a field we cannot read is simply not named
                }
            }
        }
        return named;
    }

    /** Mod Menu keeps the chosen entry in {@code selected}; its {@code mod} answers {@code getId()}. */
    private String selectedMod(Object screen) {
        for (Class<?> type = screen.getClass(); type != null && !type.getName().startsWith("net.minecraft.");
             type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField("selected");
                field.setAccessible(true);
                return modId(field.get(screen));
            } catch (ReflectiveOperationException | RuntimeException ignored) {
                // not this class
            }
        }
        return null;
    }

    /** Mod Menu entries (subclasses of its ModListEntry) hold their mod in an inherited {@code mod} field. */
    private String modId(Object entry) {
        if (entry == null || entry.getClass().getName().startsWith("net.minecraft.")) {
            return null;
        }
        try {
            Field field = field(entry.getClass(), "mod");
            Object mod = field == null ? null : field.get(entry);
            Method getId = mod == null ? null : method(mod.getClass(), "getId", 0);
            return getId == null ? null : String.valueOf(getId.invoke(mod));
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    private static Method method(Class<?> owner, String name, int parameters) {
        for (Class<?> type = owner; type != null; type = type.getSuperclass()) {
            Method found = declared(type, name, parameters);
            if (found != null) {
                return found;
            }
            for (Class<?> face : type.getInterfaces()) {
                found = declaredInInterfaces(face, name, parameters);
                if (found != null) {
                    return found;
                }
            }
        }
        return null;
    }

    private static Method declaredInInterfaces(Class<?> face, String name, int parameters) {
        Method found = declared(face, name, parameters);
        if (found != null) {
            return found;
        }
        for (Class<?> parent : face.getInterfaces()) {
            found = declaredInInterfaces(parent, name, parameters);
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    private static Method declared(Class<?> type, String name, int parameters) {
        for (Method method : type.getDeclaredMethods()) {
            if (method.getName().equals(name) && method.getParameterCount() == parameters && !method.isBridge()) {
                method.setAccessible(true);
                return method;
            }
        }
        return null;
    }

    private static Field field(Class<?> owner, String name) {
        for (Class<?> type = owner; type != null; type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField(name);
                field.setAccessible(true);
                return field;
            } catch (NoSuchFieldException ignored) {
                // keep looking in the superclass
            }
        }
        return null;
    }

    private void write(String snapshot) {
        sequence++;
        String line = "{\"seq\":" + sequence + ",\"t_ms\":" + System.currentTimeMillis() + ",\"snapshot\":" + snapshot + "}\n";
        try (OutputStream out = new FileOutputStream(output, true)) {
            out.write(line.getBytes(StandardCharsets.UTF_8));
        } catch (IOException ignored) {
            // the runner sees no new line and treats the step as unconfirmed
        }
    }

    private static String array(List<String> values) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < values.size(); i++) {
            out.append(i == 0 ? "" : ",").append(quote(values.get(i)));
        }
        return out.append(']').toString();
    }

    static String quote(String value) {
        if (value == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }
}
