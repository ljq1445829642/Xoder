"""Tests for ast_parser module"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from ast_parser import parse_file, discover_modules

def test_parse_file_python():
    code = '''
class UserService:
    def register_user(self, request):
        return UserResponse()
    
    def get_profile(self, user_id):
        pass
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        path = f.name
    try:
        result = parse_file(path)
        assert len(result["symbols"]["classes"]) >= 1
        class_names = [c["class_name"] for c in result["symbols"]["classes"]]
        assert "UserService" in class_names
    finally:
        os.unlink(path)

def test_parse_file_java():
    code = '''
@Service
@Transactional
public class OrderService {
    @Autowired
    private OrderRepository orderRepo;
    
    public Order createOrder(CreateOrderRequest req) {
        return orderRepo.save(new Order(req));
    }
}
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.java', delete=False) as f:
        f.write(code)
        path = f.name
    try:
        result = parse_file(path)
        assert len(result["symbols"]["classes"]) >= 1
        class_names = [c["class_name"] for c in result["symbols"]["classes"]]
        assert "OrderService" in class_names
    finally:
        os.unlink(path)

def test_discover_modules_temp():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "src", "user"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "src", "order"), exist_ok=True)
        open(os.path.join(tmp, "src", "user", "service.py"), 'w').close()
        open(os.path.join(tmp, "src", "order", "handler.py"), 'w').close()
        
        result = discover_modules(tmp)
        modules = result["modules"]
        assert len(modules) >= 2
        names = [m["name"] for m in modules]
        assert any("user" in n for n in names)
        assert any("order" in n for n in names)

if __name__ == "__main__":
    test_parse_file_python()
    test_parse_file_java()
    test_discover_modules_temp()
    print("All tests passed")
